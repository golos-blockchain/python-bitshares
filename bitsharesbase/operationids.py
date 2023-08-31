# -*- coding: utf-8 -*-
#: Operation ids
ops = [
    "vote",
    "comment",
    "transfer",
    "transfer_to_vesting",
    "withdraw_vesting",
    "limit_order_create",
    "limit_order_cancel"
]
operations = {o: ops.index(o) for o in ops}


def getOperationNameForId(i):
    """Convert an operation id into the corresponding string."""
    for key in operations:
        if int(operations[key]) is int(i):
            return key
    return "Unknown Operation ID %d" % i


def getOperationName(id: str):
    """This method returns the name representation of an operation given its value as
    used in the API."""
    if isinstance(id, str):
        # Some graphene chains (e.g. steem) do not encode the
        # operation_type as id but in its string form
        assert id in operations.keys(), "Unknown operation {}".format(id)
        return id
    elif isinstance(id, int):
        return getOperationNameForId(id)
    else:
        raise ValueError
