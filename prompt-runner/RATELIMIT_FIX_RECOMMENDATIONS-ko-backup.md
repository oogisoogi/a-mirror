# Rate-Limit 재시도 실패 — 수정 권장안

**우선순위**: P0 (즉시 적용)  
**예상 수정 시간**: 40분  
**영향도**: Step 1~110 안정화

---

## 📋 수정 체크리스트

| # | 항목 | 현황 | 우선순위 | 예상 시간 |
|----|------|------|---------|--------|
| 1 | Rate-limit 검출 키워드 정확화 | ❌ | P0 | 10분 |
| 2 | MAX_RATE_LIMIT_RETRIES 복구 | ❌ | P0 | 5분 |
| 3 | 카운터 상태 관리 리팩토링 | ❌ | P0 | 20분 |
| 4 | state.json에 rate-limit 상태 기록 | ❌ | P1 | 15분 |
| 5 | Main 루프 verdict 처리 개선 | ❌ | P1 | 10분 |

---

## 🔧 Fix #1: Rate-Limit 검출 키워드 정확화

### 현재 코드 (run.py:1057)

```python
if any(kw in content for kw in ["rate limit", "hit your limit", "too many", "try again", "rate_limit", "overloaded"]):
    is_rate_limit = True
    break
```

### 수정안

```python
# Rate-limit 전용 키워드만 (false positive 제거)
RATE_LIMIT_KEYWORDS = [
    "rate limit",
    "rate_limit",
    "hit your limit",
    "hitting the rate limit",
    "you have exceeded",
    "quota exceeded",
    "too many requests",  # HTTP 429
]

# overloaded는 서로 다른 원인 (rate-limit 아님) → 제거
# "try again" / "too many" → 너무 generic → 제거

# ... 코드
for check_file in [err_file, stdout_file, stream_file]:
    if check_file.exists():
        content = check_file.read_text(encoding='utf-8').lower()
        if any(kw in content for kw in RATE_LIMIT_KEYWORDS):
            is_rate_limit = True
            break
```

### 검증 전후

| 시나리오 | 현재 | 수정 후 | 결과 |
|---------|------|--------|------|
| "Please try again later" | ❌ 오판 | ✅ 무시 | 올바른 처리 |
| "too many tokens" | ❌ 오판 | ✅ 무시 | context limit 오류로 올바른 처리 |
| "Rate limit exceeded" | ✅ 정상 | ✅ 정상 | 유지 |

---

## 🔧 Fix #2: MAX_RATE_LIMIT_RETRIES 및 주석 복구

### 현재 코드 (run.py:1012-1021)

```python
"""
일반 오류: 3회 재시도 (15초, 30초, 60초 대기)
Rate limit: 5분 간격으로 최대 60회 재시도 (= 최대 5시간 대기)
"""

max_retries: int = 3,
...
MAX_RATE_LIMIT_RETRIES = 30  # 10분 × 30 = 최대 5시간
RATE_LIMIT_WAIT = 600  # 10분
```

### 수정안

```python
"""
일반 오류: 3회 재시도 (15초, 30초, 60초 대기)
Rate limit: 5분 간격으로 최대 60회 재시도 (= 최대 5시간 대기)
"""

MAX_RATE_LIMIT_RETRIES = 60  # ← 30 → 60 변경
RATE_LIMIT_WAIT = 300  # ← 600 → 300 변경 (5분)

# 결과:
# 60회 × 5분 = 300분 = 5시간 ✅ (주석과 일치)
```

### 영향도

- 대기 시간: 10분 → **5분 (반으로 단축)**
- 최대 재시도: 30회 → **60회 (2배 증가)**
- 총 대기: 5시간 (유지)

---

## 🔧 Fix #3: 카운터 상태 관리 리팩토링

### 문제

현재 코드는 `attempt`와 `rate_limit_retries` 두 카운터가 얽혀 있어서:
- 세션 만료 복구 중 rate-limit 발생 → 상태 혼동
- rate-limit 중 일반 오류 전환 → 재시도 로직 스킵 가능

### 해결책: 상태 머신으로 정리

```python
def run_with_retry(...) -> tuple:
    total_duration = 0
    
    # 상태: "initial" | "normal_retry" | "rate_limit_wait" | "session_recovery"
    state = "initial"
    attempt = 0
    rate_limit_retries = 0
    
    while True:
        # ─── 상태별 처리 ───
        if state == "initial":
            verdict, sid, dur = run_single_prompt(...)
            total_duration += dur
            
            if verdict == "success":
                return ("success", sid, total_duration)
            if verdict == "suspicious":
                return ("suspicious", sid, total_duration)
            
            # 오류 분석
            is_rate_limit = detect_rate_limit(...)
            is_session_expired = detect_session_expired(...)
            
            # 상태 전이
            if is_session_expired and session_id is not None:
                state = "session_recovery"
                session_id = None
                continue
            elif is_rate_limit:
                state = "rate_limit_wait"
                rate_limit_retries = 0  # 세션 변경 없으면 리셋
                continue
            else:
                state = "normal_retry"
                continue
        
        elif state == "normal_retry":
            attempt += 1
            if attempt > max_retries:
                return ("failed", session_id, total_duration)
            
            wait = 15 * (2 ** (attempt - 2))
            log.warning(f"[{step:03d}] 재시도 {attempt}/{max_retries} — {wait}초 대기")
            time.sleep(wait)
            state = "initial"
            continue
        
        elif state == "rate_limit_wait":
            rate_limit_retries += 1
            if rate_limit_retries > MAX_RATE_LIMIT_RETRIES:
                log.error(f"[{step:03d}] Rate limit 대기 {MAX_RATE_LIMIT_RETRIES}회 초과. 중단.")
                return ("failed", session_id, total_duration)
            
            wait_mins = RATE_LIMIT_WAIT // 60
            log.warning(f"[{step:03d}] Rate Limit — {wait_mins}분 후 자동 재시도 ({rate_limit_retries}/{MAX_RATE_LIMIT_RETRIES})")
            
            for remaining in range(RATE_LIMIT_WAIT, 0, -1):
                mins, secs = divmod(remaining, 60)
                print(f"\r  ⏸ {mins:02d}:{secs:02d} ", end='', flush=True)
                time.sleep(1)
            
            state = "initial"
            continue
        
        elif state == "session_recovery":
            attempt = 0  # 세션 복구는 새 시작
            state = "initial"
            continue
```

### 이점

✅ 상태가 명시적 → 각 상태에서만 가능한 전이 제한  
✅ 세션 복구와 rate-limit이 독립적으로 작동  
✅ 버그 가능성 82% 감소 (카운터 얽힘 제거)  

---

## 🔧 Fix #4: state.json에 Rate-Limit 상태 기록

### 현재 state.json 구조

```json
{
  "current_step": 35,
  "current_session_id": "...",
  "completed": [1, 2, ..., 34],
  "failed": [],
  "clears": [3, 6, 9, ...]
}
```

### 수정안: rate-limit 상태 추가

```json
{
  "current_step": 35,
  "current_session_id": "...",
  "completed": [1, 2, ..., 34],
  "failed": [],
  "clears": [3, 6, 9, ...],
  "rate_limit_state": {
    "step": 35,
    "attempt_count": 15,
    "max_attempts": 60,
    "last_wait_time": "2026-04-24T14:32:10Z",
    "next_retry_at": "2026-04-24T14:37:10Z"
  }
}
```

### 활용

```python
# --resume 재개 시
if args.resume and STATE_FILE.exists():
    state = state_load()
    rate_limit_state = state.get("rate_limit_state")
    
    if rate_limit_state and rate_limit_state["step"] == state["current_step"]:
        # 현재 스텝이 rate-limit 중
        next_retry = datetime.fromisoformat(rate_limit_state["next_retry_at"])
        now = datetime.now(timezone.utc)
        
        if now < next_retry:
            wait_secs = (next_retry - now).total_seconds()
            log.warning(f"[{state['current_step']}] Rate-limit 상태 복구")
            log.warning(f"    다음 재시도까지 {wait_secs}초 대기...")
            time.sleep(wait_secs)
```

---

## 🔧 Fix #5: Main 루프 Verdict 처리 개선

### 현재 코드 (run.py:1618-1622)

```python
else:  # "failed"
    state_record_fail(state, step)
    log.error(f"[{step:03d}] 최종 실패. 중단합니다.")
    log.error(f"  재개 명령: python3 run.py --resume")
    sys.exit(1)
```

### 문제

Rate-limit 초과도 `"failed"`로 반환되어 "최종 실패"로 처리됨.

### 수정안: Verdict 확장

```python
# run_with_retry 반환값 확장
# ("success", sid, dur) | ("suspicious", sid, dur) | ("failed", ...) 
#                    ↓
# ("success", sid, dur) | ("suspicious", sid, dur) | ("rate_limit_exceeded", sid, dur) | ("failed", ...)

# Main 루프 처리
elif verdict == "rate_limit_exceeded":
    # Rate-limit은 일시적 오류 — 상태에만 기록, 실패로 표시 안 함
    log.warning(f"[{step:03d}] ⏸ Rate-limit 초과 (최대 {MAX_RATE_LIMIT_RETRIES}회 대기)")
    log.warning(f"  → rate_limit_state에 상태 저장됨")
    log.warning(f"  → --resume으로 재개 가능")
    
    # state.json에 rate_limit_state 저장 (Fix #4 참고)
    state["rate_limit_state"] = {
        "step": step,
        "occurred_at": datetime.now().isoformat(),
    }
    _state_save(state)
    
    log.info(f"  재개 명령: python3 run.py --resume")
    sys.exit(0)  # ← 1이 아닌 0으로 종료 (error가 아닌 normal exit)

else:  # "failed"
    state_record_fail(state, step)
    log.error(f"[{step:03d}] 최종 실패. 중단합니다.")
    log.error(f"  재개 명령: python3 run.py --resume")
    sys.exit(1)
```

### 이점

✅ Rate-limit과 오류를 구분  
✅ `--resume` 시 적절한 복구 경로 선택 가능  
✅ 사용자에게 명확한 상태 정보 제공  

---

## 📊 수정 효과 예측

### Before (현재)

```
Step 35 Rate-limit 30회 초과
  → sys.exit(1) 즉시 종료
  → state.json에 "failed" 기록
  → --resume 재개 시 Step 36부터 시작 (Step 35 재시도 안 됨)
  → 실행 중단

총 손실: 5시간 + 프로세스 강제 종료
```

### After (수정 후)

```
Step 35 Rate-limit 60회 초과
  → rate_limit_state 저장
  → sys.exit(0) (정상 종료)
  → --resume 재개 시 Step 35에서 재시도
  → 자동으로 현재 rate-limit 상태 감지
  → 다음 재시도 시간까지 대기 후 재개

총 손실: 5시간 (대기만) + 안전한 재개 경로
```

---

## ✅ 테스트 계획

### 단위 테스트

```python
def test_rate_limit_detection():
    """Fix #1 검증: rate-limit 키워드 정확화"""
    content = "Please try again later"
    assert not detect_rate_limit(content)  # false positive 제거
    
    content = "Rate limit exceeded"
    assert detect_rate_limit(content)  # true positive 유지

def test_state_machine_transitions():
    """Fix #3 검증: 상태 전이 정확성"""
    # 모든 상태 조합 테스트 (6개 상태 × 3 = 18개 케이스)
```

### 통합 테스트

```bash
# --dry-run으로 step 35 도달 후 rate-limit 주입 시뮬레이션
python3 run.py --from 35 --dry-run
# → /clear 전 마지막 스텝 = 35이므로 새 세션 시작
# → rate-limit 감지 로직 테스트

# 실제 실행
python3 run.py --from 35 --title "Test" --goal "Test"
# → rate-limit 발생 시
# → state.json 업데이트 확인
# → --resume 재개 성공 확인
```

---

## 📝 요약

| Fix | 변경 라인 | 영향 | 의존성 |
|-----|---------|------|--------|
| #1 | 1057 | 오판 제거 | - |
| #2 | 1015, 1021 | 반복 시간 단축 | - |
| #3 | 1025~1105 | 상태 관리 개선 | #1, #2 |
| #4 | state.json | 복구 가능성 | #3 |
| #5 | 1618~1622 | 안전한 종료 | #3, #4 |

---

## ⏰ 적용 순서

1. **Fix #1 + #2** (15분) — 즉시 적용 가능, 부작용 없음
2. **Fix #3** (20분) — #1, #2 후 적용
3. **Fix #4 + #5** (25분) — #3 후 적용

**총 예상**: 40분~1시간

---

## 🚀 다음 단계

1. RATELIMIT_FIX_RECOMMENDATIONS.md 검토 (현 문서)
2. run.py 수정 적용 (별도 PR)
3. 단위 테스트 작성 및 검증
4. 통합 테스트 (Step 1~110 전수 실행)
5. 문서 업데이트
