"""
validate.py — server-side JSON Schema validation of tool arguments.

This is the FIRST of the two independent checks Defensive Tool Design
asks for: "does this call even match the declared shape" (type, required,
additionalProperties: false, enum, length/pattern/range constraints).
The SECOND check — "is this legal given the current state of the world"
(does the repository exist, does the environment belong to it, is a
deployment already in progress) — deliberately does NOT live here; it
lives in each handler in tools_impl/, against the live database, because
no schema library can know that.

Hand-rolls the small subset of JSON Schema this project's tool schemas
actually use (type, properties, required, additionalProperties, enum,
minLength, maxLength, pattern, minimum, maximum) so the sandbox this was
built in doesn't need network access to `pip install jsonschema`. The
function signature — validate(instance, schema) raising on the first
violation — mirrors `jsonschema.validate()` on purpose, so swapping in the
real library later is a one-line change in server.py, not a rewrite.
"""

import re

from mcp_server.protocol import JSONRPCError, INVALID_PARAMS


class ValidationError(JSONRPCError):
    def __init__(self, message: str):
        super().__init__(INVALID_PARAMS, message)


_TYPE_MAP = {
    "object": dict,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
}


def validate(instance, schema: dict, path: str = "$"):
    expected_type = schema.get("type")
    if expected_type:
        py_type = _TYPE_MAP.get(expected_type)
        # bool is a subclass of int in Python; don't let a bool sneak in as an integer.
        if expected_type == "integer" and isinstance(instance, bool):
            raise ValidationError(f"{path}: expected integer, got boolean")
        if py_type and not isinstance(instance, py_type):
            raise ValidationError(f"{path}: expected {expected_type}, got {type(instance).__name__}")

    if expected_type == "object" or (expected_type is None and isinstance(instance, dict)):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional_ok = schema.get("additionalProperties", True)

        for field in required:
            if field not in instance:
                raise ValidationError(f"{path}: missing required field '{field}'")

        if not additional_ok:
            unknown = set(instance.keys()) - set(properties.keys())
            if unknown:
                raise ValidationError(
                    f"{path}: unexpected field(s) not allowed by schema: {sorted(unknown)}"
                )

        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], path=f"{path}.{key}")

    elif expected_type == "string":
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise ValidationError(f"{path}: shorter than minLength={schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise ValidationError(f"{path}: longer than maxLength={schema['maxLength']}")
        if "pattern" in schema and not re.match(schema["pattern"], instance):
            raise ValidationError(f"{path}: does not match pattern {schema['pattern']!r}")
        if "enum" in schema and instance not in schema["enum"]:
            raise ValidationError(f"{path}: must be one of {schema['enum']}")

    elif expected_type in ("integer", "number"):
        if "minimum" in schema and instance < schema["minimum"]:
            raise ValidationError(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            raise ValidationError(f"{path}: must be <= {schema['maximum']}")
        if "enum" in schema and instance not in schema["enum"]:
            raise ValidationError(f"{path}: must be one of {schema['enum']}")
