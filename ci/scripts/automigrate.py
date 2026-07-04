#!/usr/bin/env python3
"""Roll the DB back to the latest migration shared by the running service and
this branch's code, so deploying a branch that lacks migrations applied by a
previous branch un-applies them before the forward `migrate` runs.

Runs `showmigrations app` on the service's CURRENT task definition (the still-
deployed OLD image, which has the files needed to reverse). Gated by the
AUTOMIGRATE_ROLLBACK env var: unless it is "true" this is a no-op, so prod never
loses data by accident.
"""

import argparse
import os
import sys
import time

import boto3

APP_LABEL = "app"
CONTAINER = "web"
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", APP_LABEL, "migrations")


def _service(ecs, cluster, service):
    return ecs.describe_services(cluster=cluster, services=[service])["services"][0]


def _run_task_logs(ecs, logs, cluster, svc, command):
    """Run one ephemeral task on the service's current task def, wait, return its log lines."""
    td_arn = svc["taskDefinition"]
    td = ecs.describe_task_definition(taskDefinition=td_arn)["taskDefinition"]
    log_opts = next(c for c in td["containerDefinitions"] if c["name"] == CONTAINER)["logConfiguration"]["options"]

    overrides = [{"name": CONTAINER, "command": command}]
    # Neutralize the cloudflared sidecar if the task def carries one: this
    # one-off task has no gunicorn on :8000, so it must not register as a
    # live tunnel connector ("version" prints and exits 0).
    if any(c["name"] == "cloudflared" for c in td["containerDefinitions"]):
        overrides.append({"name": "cloudflared", "command": ["version"]})

    task = ecs.run_task(
        cluster=cluster,
        taskDefinition=td_arn,
        launchType="FARGATE",
        networkConfiguration=svc["networkConfiguration"],
        overrides={"containerOverrides": overrides},
    )["tasks"][0]
    arn = task["taskArn"]
    task_id = arn.split("/")[-1]

    ecs.get_waiter("tasks_stopped").wait(cluster=cluster, tasks=[arn])

    # Look the container up by name — with the cloudflared sidecar present,
    # containers[0] is not guaranteed to be the web container.
    container = next(
        c for c in ecs.describe_tasks(cluster=cluster, tasks=[arn])["tasks"][0]["containers"]
        if c["name"] == CONTAINER
    )
    if container.get("exitCode") not in (0, None):
        print(f"WARNING: task exited with code {container.get('exitCode')}", file=sys.stderr)

    stream = f"{log_opts['awslogs-stream-prefix']}/{CONTAINER}/{task_id}"
    group = log_opts["awslogs-group"]
    for _ in range(10):
        try:
            events = logs.get_log_events(
                logGroupName=group,
                logStreamName=stream,
                startFromHead=True,
            )["events"]
            if events:
                return [e["message"] for e in events]
        except logs.exceptions.ResourceNotFoundException:
            pass
        time.sleep(3)
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--region", required=True)
    args = parser.parse_args()

    if os.environ.get("AUTOMIGRATE_ROLLBACK", "").lower() != "true":
        print("AUTOMIGRATE_ROLLBACK != 'true' -> skipping rollback (safe default).")
        return

    ecs = boto3.client("ecs", region_name=args.region)
    logs = boto3.client("logs", region_name=args.region)
    svc = _service(ecs, args.cluster, args.service)

    lines = _run_task_logs(ecs, logs, args.cluster, svc, ["python", "manage.py", "showmigrations", APP_LABEL])
    applied = [ln.split("[X]", 1)[1].strip() for ln in lines if "[X]" in ln]
    if not applied:
        print("No applied migrations on the running service -> nothing to roll back.")
        return

    ancestor = next(
        (name for name in reversed(applied) if os.path.exists(os.path.join(MIGRATIONS_DIR, f"{name}.py"))),
        None,
    )
    if ancestor is None:
        print("ERROR: no migration common to the DB and this branch.", file=sys.stderr)
        sys.exit(1)
    if ancestor == applied[-1]:
        print(f"DB already at common ancestor ({ancestor}) -> no rollback needed.")
        return

    print(f"Rolling back {APP_LABEL} from {applied[-1]} to common ancestor {ancestor}")
    _run_task_logs(ecs, logs, args.cluster, svc, ["python", "manage.py", "migrate", APP_LABEL, ancestor])
    print("Rollback done.")


if __name__ == "__main__":
    main()
