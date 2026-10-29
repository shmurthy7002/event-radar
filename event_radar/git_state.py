"""Independent bare Git storage with explicit bootstrap and non-forced state updates."""

import argparse
import base64
import json
import os
import re
import subprocess
from pathlib import Path

from event_radar.state import (
    State,
    StateError,
    _unique_object,
    load_state,
    parse_state,
    save_state,
    state_data,
)


class GitStateError(StateError):
    """Durable state cannot be trusted or published; do not silently replace it."""


class GitStateStore:
    def __init__(
        self, directory, remote: str, *, branch: str = "event-radar-state", token: str | None = None
    ):
        self.directory = Path(directory).resolve()
        self.branch = branch
        self.revision = None
        self._token = token
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", branch) or branch in (
            "main",
            "master",
        ):
            raise GitStateError("invalid dedicated state branch")
        if not remote or remote.startswith("-") or any(c in remote for c in "\r\n"):
            raise GitStateError("invalid state remote")
        marker = self.directory / "event-radar-store"
        if self.directory.exists() and any(self.directory.iterdir()) and not marker.is_file():
            raise GitStateError("state cache directory is not owned by this adapter")
        self.directory.mkdir(parents=True, exist_ok=True)
        if not marker.exists():
            self._run(["init", "--bare", str(self.directory)], bare=False)
            marker.write_text("event-radar-state-v1\n", encoding="utf-8")
        self._run(["check-ref-format", f"refs/heads/{branch}"])
        self._run(["config", "remote.origin.url", remote])

    def _run(self, args, *, data: bytes | None = None, bare: bool = True, allowed=(0,)):
        env = os.environ.copy()
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_AUTHOR_NAME="Event Radar",
            GIT_COMMITTER_NAME="Event Radar",
            GIT_AUTHOR_EMAIL="event-radar@users.noreply.github.com",
            GIT_COMMITTER_EMAIL="event-radar@users.noreply.github.com",
        )
        if self._token:
            header = (
                "AUTHORIZATION: basic "
                + base64.b64encode(("x-access-token:" + self._token).encode()).decode()
            )
            env.update(
                GIT_CONFIG_COUNT="1",
                GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                GIT_CONFIG_VALUE_0=header,
            )
        command = (
            ["git"]
            + (["--git-dir", str(self.directory), "-c", "commit.gpgsign=false"] if bare else [])
            + args
        )
        try:
            result = subprocess.run(
                command, input=data, capture_output=True, env=env, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            raise GitStateError("durable Git operation unavailable or timed out") from None
        if result.returncode not in allowed:
            raise GitStateError(
                "durable Git operation failed; check access, branch integrity, and push conflicts"
            )
        return result

    def _remote_head(self):
        result = self._run(
            ["ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{self.branch}"],
            allowed=(0, 2),
        )
        if result.returncode == 2:
            return None
        lines = result.stdout.decode("ascii").splitlines()
        if len(lines) != 1 or not re.fullmatch(
            r"[a-f0-9]{40,64}\s+refs/heads/" + re.escape(self.branch), lines[0]
        ):
            raise GitStateError("durable state branch identity is invalid")
        return lines[0].split()[0]

    def fetch(self) -> State:
        head = self._remote_head()
        if head is None:
            raise GitStateError(
                "durable state branch is missing; bootstrap only an intentional first run"
            )
        self._run(["fetch", "--no-tags", "origin", f"refs/heads/{self.branch}"])
        revision = self._run(["rev-parse", "FETCH_HEAD"]).stdout.decode("ascii").strip()
        if revision != head:
            raise GitStateError("durable state changed during retrieval; fetch a fresh snapshot")
        try:
            state = parse_state(
                json.loads(
                    self._run(["show", f"{revision}:state.json"]).stdout,
                    object_pairs_hook=_unique_object,
                )
            )
        except (ValueError, UnicodeError, StateError, RecursionError):
            raise GitStateError("durable state is corrupt or has an unsupported schema") from None
        self.revision = revision
        return state

    def initialize(self, state: State | None = None):
        if self._remote_head() is not None:
            raise GitStateError("durable state already exists; refusing bootstrap over history")
        self.revision = None
        return self._publish(state or State(), initialize=True)

    def publish(self, state: State):
        if self.revision is None:
            raise GitStateError("fetch trustworthy state before publishing")
        return self._publish(state, initialize=False)

    def _publish(self, state: State, *, initialize: bool):
        payload = (
            json.dumps(state_data(parse_state(state_data(state))), sort_keys=True, indent=2) + "\n"
        ).encode()
        if self._remote_head() != self.revision:
            raise GitStateError(
                "durable state head changed or disappeared; refuse stale/conflicting update"
            )
        if not initialize:
            previous = self._run(["show", f"{self.revision}:state.json"]).stdout
            if previous == payload:
                return self.revision
        blob = (
            self._run(["hash-object", "-w", "--stdin"], data=payload).stdout.decode("ascii").strip()
        )
        tree = (
            self._run(["mktree"], data=f"100644 blob {blob}\tstate.json\n".encode())
            .stdout.decode("ascii")
            .strip()
        )
        parents = ["-p", self.revision] if self.revision else []
        commit = (
            self._run(["commit-tree", tree, *parents], data=b"Update Event Radar delivery state\n")
            .stdout.decode("ascii")
            .strip()
        )
        # A fresh preflight catches ordinary conflicts; the non-forced push rejects
        # competing descendant updates racing the preflight. Never merge histories.
        if self._remote_head() != self.revision:
            raise GitStateError("durable state changed before push; refuse conflicting update")
        self._run(["push", "origin", f"{commit}:refs/heads/{self.branch}"])
        self.revision = commit
        return commit


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit durable Git state bootstrap/retrieval")
    parser.add_argument("operation", choices=("initialize", "fetch"))
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="event-radar-state")
    parser.add_argument("--directory", default="state/git-cache")
    parser.add_argument("--state", default="state/durable.json")
    parser.add_argument(
        "--from-state", help="explicit migration of existing delivery history during bootstrap"
    )
    args = parser.parse_args(argv)
    try:
        remote = args.remote
        if remote == "origin":
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"], capture_output=True, check=True, timeout=10
            )
            remote = result.stdout.decode().strip()
        store = GitStateStore(
            args.directory, remote, branch=args.branch, token=os.environ.get("GITHUB_TOKEN")
        )
        if args.operation == "initialize":
            store.initialize(load_state(args.from_state) if args.from_state else State())
        else:
            state = store.fetch()
            save_state(args.state, state, initialize=not Path(args.state).exists())
    except (StateError, OSError, subprocess.SubprocessError):
        print(
            "Durable state failed: check access, corruption, explicit bootstrap, or conflicting updates."
        )
        return 6
    print("Durable state operation completed; no email sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
