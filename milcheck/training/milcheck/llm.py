from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class LocalLLMConfig:
    base_url: str
    model: str
    api_key: str = "local-only"
    timeout_seconds: int = 120

    @classmethod
    def from_env(cls) -> "LocalLLMConfig":
        return cls(
            base_url=os.environ.get("MILCHECK_LLM_BASE_URL", "http://127.0.0.1:8000/v1"),
            model=os.environ.get("MILCHECK_LLM_MODEL", "local-model"),
            api_key=os.environ.get("MILCHECK_LLM_API_KEY", "local-only"),
        )


class LocalOpenAICompatibleLLM:
    """Optional internal-only narrative adapter.

    It cannot modify the deterministic decision. The calling agent supplies the
    fixed decision and asks only for a concise Korean explanation.
    """

    def __init__(self, config: LocalLLMConfig):
        self.config = config

    def _chat(self, system_prompt: str, user_content: str) -> str:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.config.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"].strip()
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            raise RuntimeError(f"로컬 LLM 호출 실패: {exc}") from exc

    def extract_fields(self, text: str, already: dict) -> dict:
        """자유서술에서 규칙 추출기가 놓친 필드만 보강한다.

        규칙이 이미 채운 필드는 호출자가 덮어쓰지 못하게 막으므로, 여기서는
        비어 있는 필드 후보만 제안하면 된다.
        """
        system_prompt = (
            "당신은 군 계약 사전검토 입력 보조자다. 주어진 문장에서 아래 필드만 "
            "JSON으로 추출하라. 문장에 근거가 없으면 그 필드를 생략하라. 추측하지 말라. "
            "허용 필드: estimated_price_krw_ex_vat(정수, 부가가치세 제외 원), "
            "contract_category(goods|service), "
            "proposed_type(small_amount|sole_source|urgent_security), "
            "sole_source_basis(compatibility|patented_no_substitute|"
            "original_supplier_direct_service|single_supplier), "
            "urgent_security_basis(urgent|security), quote_count_planned(정수), "
            "electronic_quotes_planned(참/거짓). JSON 객체만 출력하라."
        )
        raw = self._chat(
            system_prompt,
            json.dumps({"문장": text, "이미_추출된_필드": already}, ensure_ascii=False),
        )
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        try:
            parsed = json.loads(cleaned)
        except ValueError as exc:
            raise RuntimeError(f"LLM 추출 결과 파싱 실패: {exc}") from exc
        return parsed if isinstance(parsed, dict) else {}

    def summarize(self, payload: dict) -> str:
        system_prompt = (
            "당신은 MIL-Check 보고서 작성 보조자다. 규칙 엔진의 decision, legal_ground, "
            "findings, missing_evidence를 절대 변경하거나 약화하지 말고, 제공된 근거만으로 "
            "한국어 5문장 이내의 요약을 작성하라. 적법성을 확정하지 말라."
        )
        return self._chat(system_prompt, json.dumps(payload, ensure_ascii=False))
