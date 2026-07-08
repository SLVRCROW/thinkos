"""Shared parameter validation for tool adapters.

Each tool adapter defines a SCHEMA dict and calls validate_params()
before executing.  Returns a list of error messages (empty = valid).
"""


def validate_params(params: object, schema: dict) -> list[str]:
    """Validate *params* against *schema*.

    *schema* is a dict mapping parameter names to rule dicts::

        {
            "path":  {"required": True,  "type": str},
            "offset": {"required": False, "type": int},
        }

    Rules supported:
        required  — bool, default False
        type      — Python type object (str, int, etc.)

    Returns a list of human-readable error messages.
    An empty list means the params are valid.
    """
    errors: list[str] = []

    # 1. params must be a dict
    if not isinstance(params, dict):
        errors.append("Parameters must be an object")
        return errors

    # 2. Check required params exist
    for key, rules in schema.items():
        if rules.get("required") and key not in params:
            errors.append(f"Missing required parameter: '{key}'")

    # 3. Check types for params that are present
    for key, rules in schema.items():
        if key not in params:
            continue
        value = params[key]
        expected_type = rules.get("type")

        if expected_type is int:
            # Exact int check — reject bool (which is a subclass of int)
            if type(value) is not int:
                errors.append(
                    f"Parameter '{key}' must be of type "
                    f"{expected_type.__name__}, got {type(value).__name__}"
                )
        elif expected_type is not None:
            if not isinstance(value, expected_type):
                errors.append(
                    f"Parameter '{key}' must be of type "
                    f"{expected_type.__name__}, got {type(value).__name__}"
                )

    # 4. Reject unknown params
    known = set(schema.keys())
    for key in params:
        if key not in known:
            errors.append(f"Unknown parameter: '{key}'")

    return errors
