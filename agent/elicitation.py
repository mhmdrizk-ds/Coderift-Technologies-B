def interactive_elicitation_handler(message, requested_schema):
    print("\n" + "=" * 70)
    print("HUMAN CONFIRMATION REQUESTED (elicitation/create)")
    print("-" * 70)
    print(message)
    print("=" * 70)

    schema = requested_schema or {}
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    raw = input("Approve? [y]es / [n]o (decline) / [c]ancel: ").strip().lower()
    if raw in ("n", "no"):
        return {"action": "decline"}
    if raw in ("c", "cancel"):
        return {"action": "cancel"}

    content = {}
    for field, field_schema in properties.items():
        if field_schema.get("type") == "boolean":
            content[field] = True
        else:
            val = input(f"  {field} ({field_schema.get('description', field)}): ").strip()
            content[field] = val
    for field in required:
        content.setdefault(field, True)

    return {"action": "accept", "content": content}


def scripted_elicitation_handler(fixed_answer: dict):
    """Returns a closure that always answers with `fixed_answer` (e.g.
    {"action": "accept", "content": {"confirm": True}}). Still prints the
    prompt and the answer so the transcript shows the confirmation
    actually happening, just without requiring a live human for the
    automated/repeatable demo run."""

    def _handler(message, requested_schema):
        print("\n" + "=" * 70)
        print("HUMAN CONFIRMATION REQUESTED (elicitation/create)")
        print("-" * 70)
        print(message)
        print(f"[scripted demo response] -> {fixed_answer}")
        print("=" * 70)
        return fixed_answer

    return _handler
