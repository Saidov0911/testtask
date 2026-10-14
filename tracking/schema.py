"""Tells drf-spectacular how our authentication classes look in the OpenAPI document."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension

from .auth import IsDevice, IsParent


class GatewayAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'tracking.auth.GatewayAuthentication'
    name = ['DeviceAuth', 'ParentAuth']

    def get_security_requirement(self, auto_schema):
        # Show only the credential the endpoint actually accepts, derived from its permission.
        permissions = {type(p) for p in auto_schema.view.get_permissions()}
        if IsDevice in permissions:
            return {'DeviceAuth': []}
        if IsParent in permissions:
            return {'ParentAuth': []}
        return [{'DeviceAuth': []}, {'ParentAuth': []}]

    def get_security_definition(self, auto_schema):
        return [
            {
                'type': 'apiKey',
                'in': 'header',
                'name': 'X-Device-Id',
                'description': 'Stub for the existing auth layer: id of the authenticated child device.',
            },
            {
                'type': 'apiKey',
                'in': 'header',
                'name': 'X-Parent-Id',
                'description': 'Stub for the existing auth layer: id of the authenticated parent.',
            },
        ]


class TutorWebhookAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'tracking.auth.TutorWebhookAuthentication'
    name = 'TutorWebhookSignature'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'X-Tutor-Signature',
            'description': (
                'hex(HMAC-SHA256(secret, "<X-Tutor-Timestamp>.<raw body>")). '
                'X-Tutor-Timestamp (unix seconds) must be within 5 minutes of server time.'
            ),
        }
