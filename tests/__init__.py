"""Test package bootstrap.

The Lambda modules under `lambda/` construct their boto3 clients at import time,
so importing a test module fails with `NoRegionError` unless a region is present
in the environment. On a developer machine inside AWS a region is usually supplied
ambiently, which hid the problem; on a CI runner or a laptop with no AWS config
every module that loads Lambda code failed to import.

Setting a region here makes the suite hermetic. No credentials are needed and no
AWS call is made: constructing a client is offline, and every test that would
touch AWS patches `boto3.client` or the client object.

`setdefault` is used so a real environment or a deliberate override still wins.
"""

import os

os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
