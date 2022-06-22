import click

import prefect
from prefect.client import Client
from prefect.engine import get_default_flow_runner_class
from prefect.tasks.secrets import PrefectSecret
from prefect.utilities.graphql import with_args


@click.group(hidden=True)
def execute():
    """
    Execute flow runs.

    \b
    Usage:
        $ prefect execute [OBJECT]

    \b
    Arguments:
        flow-run  Execute a flow run with a backend API

    \b
    Examples:
        $ prefect execute flow-run
    """


@execute.command(hidden=True)
def flow_run():
    """
    Execute a flow run in the context of a backend API.

    TODO: Either deprecate this and replace with a new CLI command that calls
          `prefect.backend.execute_flow_run` or update this to call that command
          with the flow_run_id pulled from context
    """
    flow_run_id = prefect.context.get("flow_run_id")
    if not flow_run_id:
        click.echo("Not currently executing a flow within a Cloud context.")
        raise Exception("Not currently executing a flow within a Cloud context.")

    query = {
        "query": {
            with_args("flow_run", {"where": {"id": {"_eq": flow_run_id}}}): {
                "flow": {"name": True, "storage": True, "run_config": True},
                "version": True,
            }
        }
    }

    client = Client()
    result = client.graphql(query)
    flow_run = result.data.flow_run

    if not flow_run:
        click.echo(f"Flow run {flow_run_id} not found")
        raise ValueError(f"Flow run {flow_run_id} not found")

    # Set the `running_with_backend` context variable to enable logging
    with prefect.context(running_with_backend=True):
        try:
            flow_data = flow_run[0].flow
            storage_schema = prefect.serialization.storage.StorageSchema()
            storage = storage_schema.load(flow_data.storage)

            # populate global secrets
            secrets = prefect.context.get("secrets", {})
            for secret in storage.secrets:
                secrets[secret] = PrefectSecret(name=secret).run()

            with prefect.context(secrets=secrets, loading_flow=True):
                flow = storage.get_flow(flow_data.name)

            with prefect.context(secrets=secrets):
                runner_cls = get_default_flow_runner_class()
                runner_cls(flow=flow).run()

        except Exception as exc:
            msg = f"Failed to load and execute flow run: {repr(exc)}"
            state = prefect.engine.state.Failed(message=msg)
            client.set_flow_run_state(flow_run_id=flow_run_id, state=state)
            client.write_run_logs(
                dict(
                    flow_run_id=flow_run_id,  # type: ignore
                    name="execute flow-run",
                    message=msg,
                    level="ERROR",
                )
            )
            click.echo(str(exc))
            raise exc
