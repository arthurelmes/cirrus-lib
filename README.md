# Cirrus Library

A common set of functions used by the Cirrus geospatial pipeline. This library are convenience functions for interacting with the various Cirrus AWS resources (queues, Lambdas, Batch, DynamoDB, etc.) and should be preferred over writing separate code where possible.

## Installation

Cirrus-lib requires [boto3-utils](https://github.com/matthewhanson/boto3-utils), a library of convenience functions built upon [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html). Cirrus-lib is published to PyPi which is the preferred method of installing:

```bash
$ pip install cirrus-lib

# install specific version
$ pip install cirrus-lib==0.1.0

# in a requirements.txt file - the tilda (~) will install the highest compatible version
cirrus-lib~=0.1.0
```

## Modules

| Module   | Description |
| -------- | ----------- |
| catalog  | Catalog and Catalogs classes representing a Cirrus input Catalog and a set of Catalogs |
| errors   | Custom Error classes |
| statedb  | The StateDB class for interacting with the Cirrus state database (DynamoDB) |
| transfer | Convenience functions for uploading and download assets in STAC Items |
| utils    | Miscellaneous utility functions |

### catalog


### errors

The `errors` module contains a single class: `InvalidInput`, which can be thrown from a task if there is a problem with the input to the task.  The Item will be marked as INVALID in the state database rather than FAILED to avoid running it again.

### statedb

The `StateDB` class from the `statedb` module is used to interact with the Cirrus state database (DynamoDB): creating new items in the database, querying, and updating statuses. Each item in the state database represents one input catalog into Cirrus. The `StateDB` class is abstracted from what is actually stored in the DynamoDB, which is optimized for queries. The actual entry in the database can be retrieved with the `StateDB.get_dbitem()` and `StateDB.get_dbitems()` functions which are used internally, but clients should generally opt to use `StateDB.get_items()`, which translates the dbitem into:

| Field Name    | Type           | Description |
| ------------- | -------------- | ----------- |
| catid         | string         | The ID of the Cirrus input catalog |
| workflow      | string         | The name of the workflow used to process the input data |
| input_collections | [string]  | A list of all the collections used by the input items |
| output_collections | [string] | A list of the collections that may be added to by the workflow |
| state         | string         | One of: 'QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'INVALID' |
| created_at    | string         | The datetime that this item was created in the database |
| updated_at    | string         | The datetime stamp that this item's state was last updated |
| input_catalog | string         | The s3 URL of the original input catalog JSON |
| execution     | string         | A http URL to the State Machine execution for the last/current process. Does not exist if `state` is 'QUEUED' |
| error_message | string         | If `state` is FAILED or INVALID, this is the error message returned |
| output_urls   | [string]       | If `state` is COMPLETED, a list of s3 URLs of all STAC Items generated by this process.

**catid**: The catalog ID is formed by a combination of all the input collections, output collections, and the name of the workflow.

### transfer

The `transfer` module contains convenience functions for transferring data.

| Function             | Description |
| ------------------   | ----------- |
| get_s3_session       | Get a boto3-utils s3 object for transfer (used by the other functions) to/from s3 |
| download_from_http   | Download a file from an HTTP url |
| download_item_assets | For a given STAC Item download 1 or more assets to a local path |
| upload_item_assets   | For a given STAC Item upload 1 or more assets to s3 |

#### utils

This module contains miscellaneous utility functions.

| Function         | Description |
| ---------------- | ----------- |
| submit_batch_job | Start a Cirrus Batch job with a given payload |


## About
Cirrus is an open-source pipeline for processing geospatial data in AWS. Cirrus was developed by [Element 84](https://element84.com/) originally under a [NASA Access project].



