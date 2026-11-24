import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ppatch import find_command, main, render_change


@pytest.mark.parametrize("change, expected", [
    ({"op": "replace", "path": "/cpu", "old": 1500, "new": 1200}, "~ /cpu: 1500 → 1200"),
    ({"op": "add", "path": "/envs/MODE", "old": None, "new": "fast"}, '+ /envs/MODE = "fast"'),
    ({"op": "remove", "path": "/envs/MODE", "old": "fast", "new": None}, '- /envs/MODE (was "fast")'),
    # history entries carry raw patch ops: no "old", value under "value"
    ({"op": "replace", "path": "/cpu", "value": 1200}, "~ /cpu = 1200"),
    ({"op": "remove", "path": "/envs/MODE"}, "- /envs/MODE"),
])
def test_render_change(change, expected):
    assert render_change(change).strip() == expected


MESSAGE_RESULT = {"app": "storefront", "applied": True, "revision": 3,
                  "changes": [{"op": "replace", "path": "/cpu", "old": 1500, "new": 1200}]}


@patch("ppatch.call", return_value=MESSAGE_RESULT)
def test_message_sends_request_and_apply_flag(mock_call, capsys):
    main(["lower storefront cpu to 80%", "--apply", "--author", "arda"])
    mock_call.assert_called_once_with(
        "POST", "/edits", {"text": "lower storefront cpu to 80%", "apply": True, "author": "arda"})
    assert "revision #3" in capsys.readouterr().out


@patch("ppatch.call", return_value=[])
def test_history_routes_to_history_endpoint(mock_call, capsys):
    main(["history", "storefront", "--limit", "5"])
    mock_call.assert_called_once_with("GET", "/history/storefront?limit=5")
    assert "no history yet" in capsys.readouterr().out


@patch("ppatch.call", return_value={"id": 4, "reverts": 3, "patch": []})
def test_undo_routes_to_undo_endpoint(mock_call, capsys):
    main(["undo", "storefront", "--author", "arda"])
    mock_call.assert_called_once_with("POST", "/undo/storefront?author=arda")
    assert "reverted #3" in capsys.readouterr().out


@pytest.mark.parametrize("argv, expected", [
    (["history", "storefront"], 0),
    (["--author", "arda", "history", "storefront"], 2),
    (["--author", "history", "set storefront cpu"], None),  # "history" is the author here
    (["lower storefront cpu"], None),
    ([], None),
])
def test_find_command(argv, expected):
    assert find_command(argv) == expected


@patch("ppatch.call", return_value=[])
def test_author_before_subcommand_and_app_is_quoted(mock_call):
    main(["--author", "arda", "history", "my app"])
    mock_call.assert_called_once_with("GET", "/history/my%20app?limit=20")


@patch("ppatch.getpass.getuser", side_effect=OSError("no user"))
@patch("ppatch.call", return_value={"id": 1, "reverts": 0, "patch": []})
def test_unknown_os_user_falls_back_to_anonymous(mock_call, _getuser):
    main(["undo", "storefront"])
    mock_call.assert_called_once_with("POST", "/undo/storefront?author=anonymous")


def test_non_json_response_exits_cleanly(monkeypatch):
    import io

    import ppatch

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ppatch.urllib.request, "urlopen", lambda req, timeout: FakeResponse(b"<html>"))
    with pytest.raises(SystemExit, match="bad response"):
        ppatch.call("GET", "/history/storefront")


@pytest.mark.parametrize("argv, expected", [
    (["--limit", "3", "history", "storefront"], 2),
    (["--author", "a", "--limit", "3", "history", "storefront"], 4),
])
def test_find_command_skips_option_values(argv, expected):
    assert find_command(argv) == expected


@pytest.mark.parametrize("bad", ["0", "501", "-1"])
def test_limit_out_of_range_is_refused_locally(bad, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["history", "storefront", "--limit", bad])
    assert exit_info.value.code == 2


@pytest.mark.parametrize("body", [{}, [], {"detail": "weird"}, None])
def test_unexpected_200_body_exits_cleanly(body):
    with patch("ppatch.call", return_value=body), pytest.raises(SystemExit, match="bad response"):
        main(["set storefront memory to 1gb"])


@patch("ppatch.call", return_value={"app": "storefront", "applied": False, "revision": None,
                                       "changes": [], "unchanged": ["/mem/limitMiB"]})
def test_unchanged_fields_are_shown(mock_call, capsys):
    main(["set storefront memory to 2gb"])
    out = capsys.readouterr().out
    assert "/mem/limitMiB (already set)" in out and "(no changes)" in out
