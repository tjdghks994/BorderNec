# BorderNec

[English guide](README.md)

BorderNec은 검증된 대안이 동일한 업무 결과를 만들고 해외 관찰자–개인정보 관계의 strict subset을 공개할 때 성공한 도구 계획을 교체합니다.

```text
Result(candidate) == Result(current)
J(candidate) < J(current)              # strict set inclusion
```

이 저장소에는 연구 구현에서 추출한 알고리즘, 유한 계획 공간의 정확한 열거, 통제 예제, 테스트가 들어 있습니다. 판단 규칙과 탐색 동작을 재현합니다. 논문의 전체 모델 실험, 비교군, 외부 벤치마크 어댑터, 모델 가중치, 원시 실행 기록, 논문 파일은 공개 범위에서 제외했습니다.

## 예제 실행

Python 3.11 이상이 필요합니다. 알고리즘과 테스트는 Python 표준 라이브러리를 사용합니다. 저장소 최상위 폴더에서 실행합니다.

```bash
PYTHONPATH=src python3 -m bordernec demo --out outputs/demo.json
PYTHONPATH=src python3 -m bordernec exact --out outputs/exact.json
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

5회 호출 데모는 호텔 검색 작업에 대해 고정된 제안 순서를 재생합니다. 초기 계획은 해외 제공자에게 `name`과 `email`을 공개합니다. 대안은 개인정보 공개 없이 동일한 기호적 호텔 검색 결과를 반환합니다. 감사 기록에는 실패한 예약 제안, 승인된 strict-subset 교체, 이후 거부된 제안이 남습니다. 이 스크립트 예제는 알고리즘의 동작을 보여주며 언어 모델의 성능을 측정하지 않습니다.

`exact` 명령은 12개 통제 작업의 성공한 유한 계획을 열거하고 동일 결과·strict subset 증거 계획이 없는 모든 계획을 보존합니다. 제거된 계획마다 증거 계획과 제거된 관찰 관계를 기록합니다. 관찰 집합이 같거나 서로 포함되지 않는 최소 계획들은 함께 보존합니다. 독립된 golden fixture가 관찰 관계, 업무 결과, 집합 관계, frontier 소속을 검증합니다.

편집 가능한 패키지로 설치하면 `bordernec` 명령도 사용할 수 있습니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
bordernec demo --out outputs/demo.json
```

## 로컬 생성기로 탐색

`LocalBackend.generate(prompt, seed)`가 계획을 제안합니다. `CommandBackend`는 셸을 거치지 않고 로컬 실행 파일을 호출합니다. 실행 파일은 표준 입력으로 프롬프트를 읽고 표준 출력으로 JSON 제안을 반환합니다. 프롬프트에는 `A1`, `A2`와 같은 불투명한 행동 식별자가 제공됩니다.

```bash
PYTHONPATH=src python3 -m bordernec search \
  --task B02_hotel_search_oversharing \
  --command '["python3", "examples/local_generator.py"]' \
  --budget 5 --seed 19 \
  --verified-results examples/verified_results.json \
  --out outputs/search.json
```

제공된 생성기는 결정적인 인터페이스 예제입니다. 로컬 언어 모델을 사용할 때는 모델 래퍼 명령을 지정하거나 `LocalBackend`를 구현합니다. 명령 인자의 `{seed}`는 해당 호출의 생성 seed로 치환됩니다. 모델 래퍼는 추론에 이 seed를 적용해야 합니다.

`plan` 모드에서는 한 번의 모델 호출이 `{"steps":["A1", "A2"]}` 형식의 완전한 계획을 제안합니다. `stepwise` 모드에서는 호출마다 실행 가능한 행동 중 `{"action_id":"A1"}`을 선택합니다. 선택 가능한 행동이 하나인 단계는 모델 호출을 사용하지 않습니다. `--budget`은 실패한 제안까지 포함한 실제 생성기 호출 수의 상한입니다. 단계별 탐색에는 `--generation-mode stepwise`를 추가합니다.

## 결과 동일성 검증

통제 예제는 작업마다 필수 사실과 금지 사실로 표현한 하나의 기호적 결과 계약을 사용합니다. 결과 레지스트리를 제공하지 않으면 해당 작업의 성공한 계획들을 이 결과 클래스로 비교합니다. 계약에 포함되지 않은 최종 상태의 사실은 비교 대상에서 제외됩니다.

구체적인 출력이나 상태 변화를 갖는 환경에서는 신뢰하는 실행기가 초기 상태를 새로 만든 실행 또는 replay로 결과 서명을 검증해야 합니다. 작업별 서명을 `--verified-results` 또는 Python의 `verified_results` 인자로 전달합니다. 서명이 없는 계획은 거부하며, 서명이 다르면 교체할 수 없습니다. 모델이 제안한 텍스트는 결과 검증의 근거가 되지 않습니다.

```json
{
  "results": [
    {"task_id": "my_task", "steps": ["broad_action"], "signature": "verified-output-hash"},
    {"task_id": "my_task", "steps": ["narrow_action"], "signature": "verified-output-hash"}
  ]
}
```

제공된 결과 레지스트리는 통제 예제 B02의 기호적 결과를 기록합니다. 이 예제에서는 외부 연산을 replay하지 않았습니다. 레지스트리 출처, 서명 생성 방식, 초기 상태를 새로 만든 실행은 연동 환경에서 제공해야 합니다. Python의 frontier 계산과 증거 계획 비교에는 같은 `TaskDomain`의 분석 결과들을 사용해야 합니다.

## Python API

```python
from bordernec import (
    Plan, analyze_plan, enumerate_successful_plans,
    generate_counterplan_descent, jurisdictional_frontier, load_benchmark,
)

task = load_benchmark("examples/pilot_12.json").tasks[1]
verified_results = {
    ("search_with_identity",): "B02:top_three_hotels:no_reservation",
    ("search_minimal",): "B02:top_three_hotels:no_reservation",
}

plans = enumerate_successful_plans(task).plans
frontier = jurisdictional_frontier(
    [analyze_plan(task, plan) for plan in plans],
    verified_results=verified_results,
)
# frontier[0].plan.steps == ("search_minimal",)

# backend is your LocalBackend implementation.
# run = generate_counterplan_descent(
#     task, backend, call_budget=5, seed=19,
#     verified_results=verified_results,
# )
```

`is_same_result_witness`가 정확한 가지치기와 실행 중 교체에 공통으로 쓰이는 관계를 구현합니다. `find_dominating_witness`는 지배되는 계획의 구체적인 대안을 반환합니다. 탐색 기록에는 각 제안, 호출 수, seed, 프롬프트 해시, 거부 사유, 현재 계획, 제거된 관찰 관계가 남습니다. `prefix_incumbents`는 각 호출 예산 시점에 사용할 수 있는 계획을 기록합니다. 강제 실행 경로로 탐색이 종료되면 남은 예산 위치에도 확보된 계획을 반복 기록합니다.

## 관찰 의미론과 재현 범위

`J`는 data 식별자, 정보 주체, 수신 법인, 수신 법인 국가, 처리 국가로 정의한 집합입니다. 수신 법인 국가 또는 처리 국가가 컨트롤러의 국내 국가와 다르면 해당 행동에서 접근하는 개인정보를 포함합니다. 동일한 관계가 반복되면 집합 원소는 하나이며, 각 발생 기록은 감사에 사용할 수 있습니다. Retrieval, provision, delegated processing, storage에 같은 관찰 규칙을 적용합니다. 이전 방식, 목적, 수출자 식별자는 감사 메타데이터로 유지합니다.

JSON 레지스트리는 구체화된 행동, 전제조건, 효과, data 연결, 행동별 사용 횟수, 최대 계획 깊이를 제공합니다. `data_atoms`, `data_atom` 등의 필드는 연구 코드의 스키마를 유지합니다. 다른 작업 파일은 `--benchmark PATH`로 지정합니다. `--max-nodes`로 정확한 열거의 상한을 명시하며, 한도를 넘으면 부분 frontier를 반환하지 않고 오류를 보고합니다.

승인된 모든 교체는 검증된 결과를 유지하고 새로운 관계를 추가하지 않으면서 관찰 관계를 제거합니다. 제한된 예산의 탐색은 전역 최소 계획을 찾기 전에 끝날 수 있습니다. 정확한 열거는 등록된 유한 계획 공간의 frontier를 계산합니다. 두 실행 방식은 올바른 도구 등록 정보와 결과 검증에 의존합니다. 이 공개본은 등록 정보에 따른 해외 관찰 관계를 계산하며 실제 해외 서비스의 관찰 측정이나 법적 이전 허용 여부 판단은 포함하지 않습니다.

## 파일과 원본 출처

| 위치 | 내용 |
| --- | --- |
| `src/bordernec/semantics.py` | 실행, 관찰 집합, 동일 결과 증거 관계, frontier 가지치기 |
| `src/bordernec/planner.py` | 유한 계획 공간의 정확한 열거 |
| `src/bordernec/local_model.py` | seed 기반 프롬프트, 계획·단계별 반복 탐색, 로컬 생성기 인터페이스 |
| `src/bordernec/schema.py` | 등록된 작업과 계획의 스키마 |
| `examples/` | 12개 통제 작업, golden 정답, 예제 생성기, 결과 레지스트리 형식 |
| `tests/` | Golden 검증, 증거 관계 불변식, 호출 예산, CLI 연동 |
| `SOURCE_PROVENANCE.json` | 원본 파일 해시와 공개본 추출 과정의 변경 사항 |

원본 해시는 추출에 사용한 연구 구현을 식별합니다. 공개본은 관찰 관계의 식별 기준, 유한 행동 의미론, seed 기반 표현 순서, strict-subset 교체 규칙을 유지합니다. 결과 클래스별 비교를 정확한 가지치기에도 적용하며, 미완료 단계별 제안도 감사 기록에 유지합니다. 포함된 예제와 테스트로 모델이나 외부 데이터셋을 내려받지 않고 알고리즘 전체를 확인할 수 있습니다.

## 라이선스

코드는 [MIT License](LICENSE)로 배포합니다.
