import boto3
import json
import logging
import os

from boto3utils import s3
from boto3.dynamodb.conditions import Key
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List

# envvars
CATALOG_BUCKET = os.getenv('CIRRUS_CATALOG_BUCKET')

STATES = ['PROCESSING', 'COMPLETED', 'FAILED', 'INVALID']
INDEX_KEYS = {
    'state_updated': ['collections_workflow', 'state_updated'],
    'updated': ['collections_workflow', 'updated']
}

# logging
logger = logging.getLogger(__name__)


class StateDB:

    def __init__(self, table_name: str=os.getenv('CIRRUS_STATE_DB', 'test')):
        """Initialize a StateDB instance using the Cirrus State DB table

        Args:
            table_name (str, optional): The Cirrus StateDB Table name. Defaults to os.getenv('CIRRUS_STATE_DB', None).
        """
        # initialize client
        self.db = boto3.resource('dynamodb')
        self.table_name = table_name
        self.table = self.db.Table(table_name)

    def add_item(self, catalog, execution):
        """ Adds new item with state function execution """
        now = datetime.now(timezone.utc).isoformat()
        key = self.catid_to_key(catalog['id'])
        response = self.table.put_item(
            Item={
                'input_collections': key['input_collections'],
                'id': key['id'],
                'state_updated': f"PROCESSING_{now}",
                'created_at': now,
                'execution': execution
            }
        )
        logger.debug("Created DynamoDB Item", extra={'id':catalog['id']})
        return response

    def add_failed_item(self, catalog, error_message):
        """ Adds new item as failed """
        """ Adds new item with state function execution """
        now = datetime.now(timezone.utc).isoformat()
        key = self.catid_to_key(catalog['id'])
        response = self.table.put_item(
            Item={
                'input_collections': key['input_collections'],
                'id': key['id'],
                'state_updated': f"FAILED_{now}",
                'created_at': now,
                'error_message': error_message
            }
        )
        logger.debug("Created DynamoDB Item", extra={'id':catalog['id']})
        return response        

    def delete_item(self, catid: str):
        key = self.catid_to_key(catid)
        response = self.table.delete_item(Key=key)
        logger.debug("Removed DynamoDB Item", extra={'id': catid})
        return response

    def get_dbitem(self, catid: str) -> Dict:
        """Get a DynamoDB item

        Args:
            catid (str): Catalog ID

        Raises:
            Exception: Error getting item

        Returns:
            Dict: DynamoDB Item
        """
        try:
            response = self.table.get_item(Key=self.catid_to_key(catid))
            return response['Item']
        except Exception as err:
            logger.info(f"Error fetching item {catid}: {err}")
            # no such item
            return None

    def get_dbitems(self, catids: List[str]) -> List[Dict]:
        """Get multiple DynamoDB Items

        Args:
            catids (List[str]): A List of Catalog IDs

        Raises:
            Exception: Error getting items

        Returns:
            List[Dict]: A list of DynamoDB Items
        """
        try:
            resp = self.db.meta.client.batch_get_item(RequestItems={
                self.table_name: {
                    'Keys': [self.catid_to_key(id) for id in catids]
                }
            })
            items = []
            for r in resp['Responses'][self.table_name]:
                items.append(r)
            logger.debug(f"Fetched {len(items)} items")
            return items
        except Exception as err:
            msg = f"Error fetching items {catids} ({err})"
            logger.error(msg, exc_info=True)
            raise Exception(msg) from err

    def get_counts(self, collection: str, state: str=None, since: str=None,
                   index: str='state_updated', limit: int=None) -> Dict:
        """Get counts by query

        Args:
            collection (str): /-separated list of collections (input or output depending on index)
            state (Optional[str], optional): State of Items to get. Defaults to None.
            since (Optional[str], optional): Get Items since this amount of time in the past. Defaults to None.
            index (str, optional): Query this index (input_state or output_state). Defaults to 'input_state'.
            limit (int, optional): The max number to return, anything over will be reported as "<limit>+", e.g. "1000+"

        Returns:
            Dict: JSON containing counts key with counts for each state requested
        """
        counts = {}

        # make sure valid collection
        assert(index in INDEX_KEYS.keys())

        _states = [state] if state else STATES

        for state in _states:
            counts[state] = 0
            resp = self.query(collection, state, since=since, index=index, select='COUNT')
            counts[state] = resp['Count']
            while 'LastEvaluatedKey' in resp:
                resp = self.query(collection, state, since=since, index=index, select='COUNT',
                                         ExclusiveStartKey=resp['LastEvaluatedKey'])
                counts[state] += resp['Count']
                if limit and counts[state] > limit:
                    break
            if limit and counts[state] > limit:
                counts[state] = f"{limit}+"
                continue
        return {
            INDEX_KEYS[index]: collection,
            'index': index,
            'counts': counts
        }

    def get_items_page(self, collection: str, state: str, since: Optional[str]=None,
                  index: str='input_state', limit=100, nextkey: str=None) -> List[Dict]:
        """Get Items by query

        Args:
            collection (str): /-separated list of collections (input or output depending on index)
            state (str): State of Items to get (PROCESSING, COMPLETED, FAILED, INVALID)
            since (Optional[str], optional): Get Items since this amount of time in the past. Defaults to None.
            index (str, optional): Query this index (input_state or output_state). Defaults to 'input_state'.

        Returns:
            Dict: List of Items
        """
        if state:
            _states = [state]
        _states = [state] if state else STATES

        items = {
            'items': []
        }
        if nextkey:
            dbitem = self.get_dbitem(nextkey)
            startkey = { key: dbitem[key] for key in ['input_collections', 'id', 'state_updated']}
            resp = self.query(collection, state, since=since, index=index, Limit=limit, ExclusiveStartKey=startkey)
        else:
            resp = self.query(collection, state, since=since, index=index, Limit=limit)
        for i in resp['Items']:
            items['items'].append(self.dbitem_to_item_legacy(i))
        if 'LastEvaluatedKey' in resp:
            items['nextkey'] = self.key_to_catid(resp['LastEvaluatedKey'])
        return items

    def get_items(self, *args, limit=None, **kwargs) -> Dict:
        """Get items from database

        Args:
            limit (int, optional): Maximum number of items to return. Defaults to None.

        Returns:
            Dict: StateDB Items
        """
        resp = self.get_items_page(*args, **kwargs)
        items = resp['items']
        while 'nextkey' in resp and (limit is None or len(items) < limit):
            resp = self.get_items_page(*args, nextkey=resp['nextkey'], **kwargs)
            items += resp['items']
        if limit is None or len(items) < limit:
            return items
        return items[:limit]

    def get_state(self, catid: str) -> str:
        """Get current state of Item

        Args:
            catid (str): The catalog ID

        Returns:
            str: Current state: PROCESSING, COMPLETED, FAILED, INVALID
        """
        response = self.table.get_item(Key=self.catid_to_key(catid))
        if 'Item' in response:
            return response['Item']['state_updated'].split('_')[0]
        else:
            # assuming no such item in database
            return ""

    def get_states(self, catids: List[str]) -> Dict[str, str]:
        """Get current state of items

        Args:
            catids (List[str]): List of catalog IDs

        Returns:
            Dict[str, str]: Dictionary of catalog IDs to state
        """
        states = {}
        for dbitem in self.get_dbitems(catids):
            item = self.dbitem_to_item_legacy(dbitem)
            states[item['catid']] = item['state']
        return states

    def set_processing(self, catid: str, execution: str) -> str:
        """Set Item to PROCESSING

        Args:
            catid (str): A Cirrus catalog
            execution (str): An ARN to the State Machine execution

        Returns:
            str: DynamoDB response
        """
        response = self.table.update_item(
            Key=self.catid_to_key(catid),
            UpdateExpression='SET state_updated=:p, execution=:exe',
            ExpressionAttributeValues={
                ':p': f"PROCESSING_{datetime.now(timezone.utc).isoformat()}",
                ':exe': execution
            }
        )
        return response

    def set_completed(self, catid: str, items: List[str]) -> str:
        """Set this catalog as COMPLETED

        Args:
            catid (str): The Cirrus Catalog
            items ([str]): List of URLs to output items

        Returns:
            str: DynamoDB response
        """
        response = self.table.update_item(
            Key=self.catid_to_key(catid),
            UpdateExpression='SET state_updated=:p, outputs=:items',
            ExpressionAttributeValues={
                ':p': f"COMPLETED_{datetime.now(timezone.utc).isoformat()}",
                ':items': items
            }
        )
        return response

    def set_failed(self, catid: str, msg: str) -> str:
        """Set this catalog as FAILED

        Args:
            catid (str): The Cirrus Catalog
            msg (str): An error message to include in DynamoDB Item

        Returns:
            str: DynamoDB response
        """
        response = self.table.update_item(
            Key=self.catid_to_key(catid),
            UpdateExpression='SET state_updated=:p, error=:err',
            ExpressionAttributeValues={
                ':p': f"FAILED_{datetime.now(timezone.utc).isoformat()}",
                ':err': msg
            }
        )
        return response

    def set_invalid(self, catid: str, msg: str) -> str:
        """Set this catalog as INVALID

        Args:
            catid (str): The Cirrus Catalog
            msg (str): An error message to include in DynamoDB Item

        Returns:
            str: DynamoDB response
        """
        response = self.table.update_item(
            Key=self.catid_to_key(catid),
            UpdateExpression='SET state_updated=:p, error=:err',
            ExpressionAttributeValues={
                ':p': f"INVALID_{datetime.now(timezone.utc).isoformat()}",
                ':err': msg
            }
        )
        return response

    def query(self, hash: str, range: str=None, since: str=None,
                     index: str='input_state', select: str='ALL_ATTRIBUTES', **kwargs) -> Dict:
        """Perform a single Query on a DynamoDB index

        Args:
            hash (str): The complete has to query
            range (str, optional): The range key to query using begins_with. Defaults to None.
            since (str, optional): Query for items since this time. Defaults to None.
            index (str, optional): The DynamoDB index to query (input_state, output_state). Defaults to 'input_state'.
            select (str, optional): DynamoDB Select statement (ALL_ATTRIBUTES, COUNT). Defaults to 'ALL_ATTRIBUTES'.

        Returns:
            Dict: DynamoDB response
        """
        expr = Key(INDEX_KEYS[index][0]).eq(hash)
        if state and since:
            start = datetime.now(timezone.utc) - self.since_to_timedelta(since)
            begin = f"{state}_{start.isoformat()}"
            end = f"{state}_{datetime.now(timezone.utc).isoformat()}"
            expr = expr & Key('state_updated').between(begin, end)
        elif state:
            expr = expr & Key('state_updated').begins_with(state)
        resp = self.table.query(IndexName=index, KeyConditionExpression=expr, Select=select, **kwargs)
        return resp

    @classmethod
    def catid_to_key(cls, catid: str) -> Dict:
        """Create DynamoDB Key from catalog ID

        Args:
            catid (str): The catalog ID

        Returns:
            Dict: Dictionary containing the DynamoDB Key
        """
        parts1 = catid.split('/workflow-')
        parts2 = parts1[1].split('/', maxsplit=1)
        key = {
            'collections_workflow': parts1[0] + f"_{parts2[0]}",
            'items': parts2[1]
        }
        return key

    @classmethod
    def key_to_catid(cls, key: Dict) -> str:
        """Get catalog ID given a DynamoDB Key

        Args:
            key (Dict): DynamoDB Key

        Returns:
            str: Catalog ID
        """
        parts = key['collections_workflow'].rsplit('_', maxsplit=1)
        return f"{parts[0]}/workflow-{parts[1]}/{key['items']}"

    @classmethod
    def get_input_catalog_url(self, dbitem):
        catid = self.key_to_catid(dbitem)
        return f"s3://{CATALOG_BUCKET}/{catid}/input.json"

    @classmethod
    def dbitem_to_item_legacy(cls, dbitem: Dict, region: str=os.getenv('AWS_REGION', 'us-west-2')) -> Dict:
        state, updated = dbitem['state_updated'].split('_')
        collections, workflow = dbitem['collections_workflow'].rsplit('_', maxsplit=1)
        item = {
            "catid": cls.key_to_catid(dbitem),
            "workflow": workflow,
            "input_collections": collections,
            "state": state,
            "created_at": dbitem['created'],
            "updated_at": updated,
            "input_catalog": cls.get_input_catalog_url(dbitem)
        }
        if 'execution' in dbitem:
            exe_url = f"https://{region}.console.aws.amazon.com/states/home?region={region}#/executions/details/{dbitem['execution'][-1]}"
            item['execution'] = exe_url
        if 'error_message' in dbitem:
            item['error'] = dbitem['error']
        if 'outputs' in dbitem:
            item['items'] = dbitem['outputs']
        return item

    @classmethod
    def since_to_timedelta(cls, since: str) -> timedelta:
        """Convert a `since` field to a timedelta.

        Args:
            since (str): Contains an integer followed by a unit letter: 'd' for days, 'h' for hours, 'm' for minutes

        Returns:
            timedelta: [description]
        """
        unit = since[-1]
        # days, hours, or minutes
        assert(unit in ['d', 'h', 'm'])
        days = int(since[0:-1]) if unit == 'd' else 0
        hours = int(since[0:-1]) if unit == 'h' else 0
        minutes = int(since[0:-1]) if unit == 'm' else 0
        return timedelta(days=days, hours=hours, minutes=minutes)
