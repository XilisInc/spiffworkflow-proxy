import json
import re
import typing
from typing import Any

from flask import Blueprint
from flask import Response
from flask import current_app
from flask import redirect
from flask import request
from flask import session
from flask import url_for
from flask import copy_current_request_context
from flask_oauthlib.contrib.client import OAuth  # type: ignore
from spiffworkflow_connector_command.command_interface import ConnectorProxyResponseDict

from spiffworkflow_proxy.plugin_service import PluginService

proxy_blueprint = Blueprint('proxy_blueprint', __name__)


@proxy_blueprint.route('/')
def index() -> str:
    return "This is the SpiffWorkflow Connector.   Point SpiffWorkfow-backend configuration to this url." \
           " Please see /v1/commands for a list of commands this connector proxy will allow."


@proxy_blueprint.route("/liveness")
def status_deprecated() -> Response:
    return Response(json.dumps({"ok": True}), status=200, mimetype="application/json")


@proxy_blueprint.route("/v1/liveness")
def status() -> Response:
    return Response(json.dumps({"ok": True}), status=200, mimetype="application/json")


@proxy_blueprint.route("/v1/commands")
def list_commands() -> Response:
    return list_targets(PluginService.available_commands_by_plugin())


@proxy_blueprint.route("/v1/do/<plugin_display_name>/<command_name>", methods=["GET", "POST"])
def do_command(plugin_display_name: str, command_name: str) -> Response:
    command = copy_current_request_context(
        PluginService.command_named(plugin_display_name, command_name)
    )
    if command is None:
        return json_error_response(
            message="It either does not exist or does not inherit from spiffworkflow_connector_command.",
            error_code="command_not_found",
            status=404
        )

    params = typing.cast(dict, request.json)
    task_data = params.pop('spiff__task_data', '{}')

    try:
        result = command(**params).execute(current_app.config, task_data)
    except Exception as e:
        return json_error_response(
            message=str(e),
            error_code=e.__class__.__name__,
            status=500
        )

    if "command_response_version" in result and result["command_response_version"] > 1:  # type: ignore
        response = json.dumps(result)
        return Response(response, mimetype='application/json', status=200)
    else:
        status_code = 200
        if 'status' in result:
            status_code = int(result['status'])  # type: ignore
        if isinstance(result["response"], dict):  # type: ignore
            response = json.dumps(result["response"])  # type: ignore
        else:
            response = result["response"]  # type: ignore
        return Response(response, mimetype=result["mimetype"], status=status_code)  # type: ignore


@proxy_blueprint.route("/v1/auths")
def list_auths() -> Response:
    return list_targets(PluginService.available_auths_by_plugin())


@proxy_blueprint.route("/v1/auth/<plugin_display_name>/<auth_name>")
def do_auth(plugin_display_name: str, auth_name: str) -> Any:
    params = request.args.to_dict()
    our_redirect_url = params["redirect_url"]
    session["redirect_url"] = our_redirect_url

    handler = auth_handler(plugin_display_name, auth_name)
    if handler is None:
        return Response("Auth not found", status=404)

    # TODO factor into handler
    # TODO namespace the keys
    session["client_id"] = current_app.config["CONNECTOR_PROXY_XERO_CLIENT_ID"]
    session["client_secret"] = current_app.config["CONNECTOR_PROXY_XERO_CLIENT_SECRET"]

    oauth_redirect_url = url_for(
        "proxy_blueprint.auth_callback",
        plugin_display_name=plugin_display_name,
        auth_name=auth_name,
        _external=True,
    )
    return handler.authorize(callback_uri=oauth_redirect_url)


@proxy_blueprint.route("/v1/auth/<plugin_display_name>/<auth_name>/callback")
def auth_callback(plugin_display_name: str, auth_name: str) -> Response:
    handler = auth_handler(plugin_display_name, auth_name)
    if handler is None:
        return Response("Auth not found", status=404)

    response = json.dumps(handler.authorized_response())
    redirect_url = session["redirect_url"]

    # TODO compare redirect_url to whitelist

    redirect_url_params_symbol = "?"
    if re.match(r".*\?.*", redirect_url):
        redirect_url_params_symbol = "&"

    return redirect(f"{redirect_url}{redirect_url_params_symbol}response={response}")  # type: ignore


def list_targets(targets: dict[str, dict[str, type]]) -> Response:
    descriptions = []

    for plugin_name, plugin_targets in targets.items():
        for target_name, target in plugin_targets.items():
            description = PluginService.describe_target(
                plugin_name, target_name, target
            )
            descriptions.append(description)

    return Response(json.dumps(descriptions), status=200, mimetype="application/json")


def auth_handler(plugin_display_name: str, auth_name: str) -> Any:
    auth = PluginService.auth_named(plugin_display_name, auth_name)
    if auth is not None:
        app_description = auth().app_description(current_app.config)

        # TODO right now this assumes Oauth.
        # would need to expand if other auth providers are used
        handler = OAuth(current_app).remote_app(**app_description)

        @handler.tokengetter  # type: ignore
        def tokengetter() -> None:
            pass

        @handler.tokensaver  # type: ignore
        def tokensaver(token: str) -> None:
            pass

        return handler


def json_error_response(message: str, error_code: str, status: int) -> Response:
    response: ConnectorProxyResponseDict = {
        "command_response": {},
        "error": {
            "message": message,
            "error_code": error_code,
        },
    }
    return Response(json.dumps(response), status=status)

