# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from knack.log import get_logger
from knack.util import CLIError
from azure.cli.core.commands import LongRunningOperation

from ._constants import ACR_TASK_YAML_DEFAULT_NAME, ORYX_PACK_BUILDER_IMAGE
from ._stream_utils import stream_logs
from ._utils import (
    validate_managed_registry,
    get_validate_platform,
    get_custom_registry_credentials,
    get_yaml_and_values
)
from ._client_factory import cf_acr_registries
from ._archive_utils import upload_source_code, check_remote_source_code
from .run import prepare_source_location

PACK_NOT_SUPPORTED = 'Pack is only available for managed registries.'
PACK_TASK_YAML_FMT = '''steps:
  - cmd: mcr.microsoft.com/oryx/pack:stable build {image_name} --builder {builder} --env REGISTRY_NAME={{{{.Run.Registry}}}} -p .
    timeout: 28800
  - push: ["{image_name}"]
    timeout: 1800
'''

logger = get_logger(__name__)


def acr_pack(cmd,  # pylint: disable=too-many-locals
             client,
             registry_name,
             image_name,
             source_location,
             builder=ORYX_PACK_BUILDER_IMAGE,
             no_format=False,
             no_logs=False,
             no_wait=False,
             timeout=None,
             resource_group_name=None,
             platform=None,
             auth_mode=None):

    _, resource_group_name = validate_managed_registry(
        cmd, registry_name, resource_group_name, PACK_NOT_SUPPORTED)

    client_registries = cf_acr_registries(cmd.cli_ctx)
    source_location = prepare_source_location(
        source_location, client_registries, registry_name, resource_group_name)
    if not source_location:
        raise CLIError('Building with Buildpacks requires a valid source location.')

    platform_os, platform_arch, platform_variant = get_validate_platform(cmd, platform)
    OS = cmd.get_models('OS')
    if platform_os != OS.linux.value.lower():
        raise CLIError('Building with Buildpacks is only supported on Linux.')

    EncodedTaskRunRequest, PlatformProperties = cmd.get_models('EncodedTaskRunRequest', 'PlatformProperties')

    yaml_body = PACK_TASK_YAML_FMT.format(image_name=image_name, builder=builder)
    import base64
    request = EncodedTaskRunRequest(
        encoded_task_content=base64.b64encode(yaml_body.encode()).decode(),
        source_location=source_location,
        timeout=timeout,
        platform=PlatformProperties(
            os=platform_os,
            architecture=platform_arch,
            variant=platform_variant
        ),
        credentials=get_custom_registry_credentials(
            cmd=cmd,
            auth_mode=auth_mode
        )
    )

    queued = LongRunningOperation(cmd.cli_ctx)(client_registries.schedule_run(
        resource_group_name=resource_group_name,
        registry_name=registry_name,
        run_request=request))

    run_id = queued.run_id
    logger.warning("Queued a run with ID: %s", run_id)

    if no_wait:
        return queued

    logger.warning("Waiting for an agent...")

    if no_logs:
        from ._run_polling import get_run_with_polling
        return get_run_with_polling(cmd, client, run_id, registry_name, resource_group_name)

    return stream_logs(client, run_id, registry_name, resource_group_name, no_format, True)
