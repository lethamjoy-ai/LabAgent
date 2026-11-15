"""Thin typed tool callable, reusable by a future scheduling agent."""
from .gateway import AppointmentGateway
from .models import GatewayRequest, Result


class AppointmentTools:
    def __init__(self, gateway: AppointmentGateway):
        self.gateway = gateway

    def invoke(self, request: GatewayRequest) -> Result:
        return self.gateway.execute(request)
