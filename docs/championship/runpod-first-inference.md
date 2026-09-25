# RunPod 첫 추론 절차 (P3)

목적: 실제 로컬 LLM이 `llm-contract-v1` 경계를 통과하는 JSON을 내는지, 합성 개발세트 10묶음에서 처음으로 측정한다.
보내는 자료: 이 저장소의 공개 코드와 **합성** 개발세트뿐. 군 자료·개인정보·공개 CSV 원본은 올리지 않는다.
모델 서버는 Pod 내부 `127.0.0.1`에만 연다(외부 포트 공개 안 함).

## 1. Pod 설정

| 항목 | 값 |
|---|---|
| GPU | RTX A5000 24GB 1장 (없으면 L4 또는 A40) |
| 템플릿 | RunPod PyTorch (CUDA 12.x) |
| Container Disk | 40 GB |
| Volume Disk | 0 GB (시험 후 삭제하므로 불필요) |
| 노출 포트 | 추가하지 않음 |

## 2. Pod 웹 터미널에서 실행

```bash
pip install -U vllm
git clone -b work/championship-rebuild https://github.com/AnonymousHeretic/MIL_Check.git
nohup vllm serve Qwen/Qwen3-4B --host 127.0.0.1 --port 8000 \
  --max-model-len 16384 --gpu-memory-utilization 0.85 > vllm.log 2>&1 &
# 준비 확인(모델 이름이 보일 때까지 1~2분 간격 반복)
curl -s http://127.0.0.1:8000/v1/models
```

```bash
cd MIL_Check
MILCHECK_LLM_MODEL=Qwen/Qwen3-4B \
MILCHECK_LLM_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}' \
python scripts/eval_understanding.py --mode live --output live-qwen3-4b.json
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv
pip show vllm | head -2
```

결과 파일 `live-qwen3-4b.json`의 내용 전체와 위 두 명령의 출력을 기록으로 남긴다.

## 3. 끝나면 반드시

Pod를 **Terminate**(Stop이 아님)해 과금을 멈춘다.

## 해석 규칙

- 합성 개발세트 결과다. 최종 성능·군 현장 성능으로 인용하지 않는다.
- 모델 revision·양자화·vLLM 버전을 결과와 함께 기록한다. 이번 실행은 revision을 고정하지 않은 **기술 시험**이다.
- Qwen3-4B는 기술시험용 후보이며 군 배포 모델 선정이 아니다.
