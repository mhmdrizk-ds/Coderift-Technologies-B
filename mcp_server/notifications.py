"""
notifications.py — server -> client notifications.

Right now there's exactly one trigger in this server: an engineer's role
changing (via authenticate) changes which tools it's allowed to see, so we
push notifications/tools/list_changed instead of leaving the client to
guess or poll tools/list on a timer. If a future tool changes state that
affects tool visibility again (e.g. an access code being deactivated
mid-session) it should call this same helper — that's the point of
keeping it in one place instead of inlining send_message calls all over
tools_impl/.
"""

from mcp_server import protocol


def send_tools_list_changed():
    protocol.send_message(protocol.make_notification("notifications/tools/list_changed"))
