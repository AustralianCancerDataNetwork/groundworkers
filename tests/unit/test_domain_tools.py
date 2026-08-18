

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.services.domain import DomainService
from groundworkers.tools.domain_tools import register_domain_tools


class FakeLLMAdapter:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def complete_structured(
        self,
        prompt: str,
        response_schema: dict,
        *,
        system_prompt: str | None = None,
        model_name: str | None = None,
        temperature: float = 0.0,
    ) -> dict:
        self.calls.append(
            {
                "prompt": prompt,
                "response_schema": response_schema,
                "system_prompt": system_prompt,
                "model_name": model_name,
                "temperature": temperature,
            }
        )
        return self.response


class StubDomainService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def classify_attributes(
        self,
        label_values: dict[str, list[str]],
        model_name: str | None = None,
    ) -> dict[str, str]:
        self.calls.append({"label_values": label_values, "model_name": model_name})
        return {"haemoglobin": "Measurement"}


def test_domain_service_filters_null_and_invalid_domains() -> None:
    llm = FakeLLMAdapter(
        {
            "haemoglobin": "Measurement",
            "smoking status": "Observation",
            "free text note": None,
            "mystery": "NotADomain",
        }
    )
    service = DomainService(llm)  # type: ignore[arg-type]

    result = service.classify_attributes(
        {
            "haemoglobin": ["12.1", "13.4"],
            "smoking status": ["current", "former"],
            "free text note": [],
            "mystery": [],
        },
        model_name="demo-model",
    )

    assert result == {
        "haemoglobin": "Measurement",
        "smoking status": "Observation",
    }
    assert llm.calls[0]["model_name"] == "demo-model"
    assert "haemoglobin" in llm.calls[0]["prompt"]


def test_domain_classify_tool_returns_classifications() -> None:
    service = StubDomainService()
    server = GroundcrewServer("test-server")
    register_domain_tools(server, service)  # type: ignore[arg-type]

    result = server.call(
        "domain_classify",
        label_values={"haemoglobin": ["12.1", "13.4"]},
        model_name="demo-model",
    )

    assert result == {"classifications": {"haemoglobin": "Measurement"}}
    assert service.calls == [
        {
            "label_values": {"haemoglobin": ["12.1", "13.4"]},
            "model_name": "demo-model",
        }
    ]


def test_domain_classify_tool_returns_groundworkers_error_dict() -> None:
    class ErrorDomainService(StubDomainService):
        def classify_attributes(self, label_values: dict[str, list[str]], model_name: str | None = None) -> dict[str, str]:
            raise GroundworkersError("BACKEND_UNAVAIL", "llm unavailable")

    server = GroundcrewServer("test-server")
    register_domain_tools(server, ErrorDomainService())  # type: ignore[arg-type]

    result = server.call("domain_classify", label_values={"haemoglobin": ["12.1"]})

    assert result == {
        "error": True,
        "code": "BACKEND_UNAVAIL",
        "message": "llm unavailable",
    }
