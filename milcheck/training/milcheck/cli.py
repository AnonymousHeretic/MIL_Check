from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import MilCheckAgent
from .evaluation import run_all


def review_command(args: argparse.Namespace) -> int:
    agent = MilCheckAgent(llm_mode=args.llm_mode)
    case = agent.load_case(args.case)
    report = agent.review(case, top_k=args.top_k)
    text = json.dumps(report, ensure_ascii=False, indent=2) if args.json \
        else agent.to_markdown(report)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(str(Path(args.output).resolve()))
    else:
        print(text)
    return 0


def intake_command(args: argparse.Namespace) -> int:
    """자유서술 문장을 구조화 필드로 변환하고, 이어서 검토까지 수행한다."""
    agent = MilCheckAgent(llm_mode=args.llm_mode)
    text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else args.text
    result = agent.intake(text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.review:
        print()
        print(agent.to_markdown(agent.review(result["fields"])))
    return 0


def evaluate_command(args: argparse.Namespace) -> int:
    agent = MilCheckAgent(llm_mode="none", use_ml=not args.no_ml,
                          use_contract_index=False)
    result = run_all(agent)
    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(Path(args.output).resolve()))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["L1_rules"]["failures"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MIL-Check 오프라인 우선 사전검토")
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review", help="단일 사례 사전검토")
    review.add_argument("case", help="입력 JSON 파일")
    review.add_argument("--output", "-o", help="보고서 저장 경로")
    review.add_argument("--top-k", type=int, default=5)
    review.add_argument("--json", action="store_true", help="JSON으로 출력")
    review.add_argument("--llm-mode", choices=["none", "local"], default="none")
    review.set_defaults(func=review_command)

    intake = sub.add_parser("intake", help="자유서술 → 구조화 필드 추출")
    intake.add_argument("text", nargs="?", default="", help="상황 설명 문장")
    intake.add_argument("--text-file", help="문장이 담긴 파일")
    intake.add_argument("--review", action="store_true", help="추출 후 바로 검토")
    intake.add_argument("--llm-mode", choices=["none", "local"], default="none")
    intake.set_defaults(func=intake_command)

    evaluate = sub.add_parser("evaluate", help="3층 평가 스위트 실행")
    evaluate.add_argument("--output", "-o", help="결과 JSON 저장 경로")
    evaluate.add_argument("--no-ml", action="store_true", help="ML 계층 제외")
    evaluate.set_defaults(func=evaluate_command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
