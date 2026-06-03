# Rate-Limit 재시도 실패 분석 보고서

**작성일**: 2026-04-24  
**심각도**: 🔴 Critical (실행 중단 야기)  
**영향 범위**: Step 1~110 전체

---

## 1️⃣ 핵심 문제: 주석과 코드 불일치

### 문제 코드 (run.py:1012-1021)

```python
"""
일반 오류: 3회 재시도 (15초, 30초, 60초 대기)
Rate limit: 5분 간격으로 최대 60회 재시도 (= 최대 5시간 대기)
"""

total_duration = 0
rate_limit_retries = 0
MAX_RATE_LIMIT_RETRIES = 30  # 10분 × 30 = 최대 5시간
RATE_LIMIT_WAIT = 600  # 10분
```

### 모순점

| 항목 | 주석 | 실제 코드 | 결과 |
|------|------|---------|------|
| **대기 간격** | 5분 | `RATE_LIMIT_WAIT = 600` (10분) | ❌ **2배 길어짐** |
| **최대 재시도** | 60회 | `MAX_RATE_LIMIT_RETRIES = 30` | ❌ **2배 적음** |
| **최대 대기 시간** | 5시간 | 30 × 10분 = **300분 = 5시간** | ✅ 일치 (우연) |

**영향**: 사용자 기대 (5분마다 시도 × 60회)와 실제 동작 (10분마다 시도 × 30회)이 크게 다름.

---

## 2️⃣ Rate-Limit 검출 키워드 오판 문제

### 문제 코드 (run.py:1057)

```python
if any(kw in content for kw in ["rate limit", "hit your limit", "too many", "try again", "rate_limit", "overloaded"]):
    is_rate_limit = True
```

### 오판 사례

| 키워드 | Rate-Limit인가 | 발생 가능한 다른 상황 |
|--------|---|---|
| `"rate limit"` | ✅ 명확 | - |
| `"hit your limit"` | ✅ 명확 | - |
| `"rate_limit"` | ✅ 명확 | - |
| `"overloaded"` | ⚠️ 애매 | API 서버 과부하 (rate-limit 아님) |
| `"too many"` | ❌ **위험** | "too many tokens", "too many open files", "too many requests" (다양한 의미) |
| `"try again"` | ❌ **매우 위험** | **모든 재시도 권장 에러**에 포함 가능 |

### 실제 해악 시나리오

```
Step 10 실행 → timeout 발생 → 로그에 "Please try again later"
↓
키워드 "try again" 감지 → is_rate_limit = True (잘못된 판정)
↓
10분 대기 후 재시도 (불필요한 시간 낭비)
↓
반복되면 수 시간이 낭비됨
```

---

## 3️⃣ 재시도 카운터 상태 관리 버그

### 문제 구조 (run.py:1025-1105)

```python
while True:
    attempt += 1
    
    if attempt > 1 and rate_limit_retries == 0:  # [A]
        if attempt > max_retries:
            break
        # 일반 재시도 대기
        time.sleep(...)
    
    verdict, sid, dur = run_single_prompt(...)
    
    if verdict == "success":
        return ("success", sid, total_duration)
    
    # [오류 분석 구간]
    is_rate_limit = False
    # ... rate-limit 검출
    
    if is_session_expired and session_id is not None:
        session_id = None
        attempt = 0         # [B] 세션 재개
        rate_limit_retries = 0
        continue
    
    if is_rate_limit:
        rate_limit_retries += 1
        if rate_limit_retries > MAX_RATE_LIMIT_RETRIES:
            break  # [C] 무조건 루프 탈출
        # 10분 대기
        time.sleep(...)
        attempt = 0  # [D] rate-limit 재시도
        continue
    
    rate_limit_retries = 0  # [E] 일반 오류
```

### 상태 전이 분석 및 문제점

#### 시나리오 A: Rate-Limit이 여러 번 연속 발생

```
Turn 1: attempt=1, rate_limit_retries=0
  → [A] 조건 false (attempt <= 1)
  → run → rate-limit 감지
  → [D] attempt=0

Turn 2: attempt=1, rate_limit_retries=1
  → [A] 조건 false (attempt <= 1)
  → run → rate-limit 감지
  → [D] attempt=0

...

Turn 30: attempt=1, rate_limit_retries=29
  → 정상 작동

Turn 31: attempt=1, rate_limit_retries=30
  → [C] rate_limit_retries (30) > MAX (30) 이후 값 체크
  ✅ break → ("failed", ...) 반환
```

**문제**: 이 부분은 **논리적으로 정상**이지만, **다음 문제와 연계되면 위험**.

#### 시나리오 B: Rate-Limit → 일반 오류 전환

```
Turn 1: rate-limit 감지 → rate_limit_retries=1, attempt=0
Turn 2: 또 rate-limit → rate_limit_retries=2, attempt=0
Turn 3: 이번엔 일반 오류 → [E] rate_limit_retries=0 리셋

Turn 4: attempt=1, rate_limit_retries=0
  → [A] 조건 true (attempt > 1 && rate_limit_retries == 0)는 false
     (attempt = 1이므로)
  → run → 또 일반 오류
  → [E] rate_limit_retries=0

... (attempt는 계속 1로 초기화되지 않음)
```

**문제**: 일반 재시도 대기([A] 블록)이 실행되지 않을 수 있음!

#### 시나리오 C: 세션 만료 + Rate-Limit 혼합

```
Step 10:
  Turn 1: rate-limit 감지 → rate_limit_retries=1, attempt=0
  Turn 2: 세션 만료 감지!
    → [B] session_id=None, attempt=0, rate_limit_retries=0
    → continue (새 세션으로 재시도)
  Turn 3: 또 rate-limit?
    → rate_limit_retries=1 (재시작)
    → [A] 조건 false
    → continue
  
  ... (세션 만료 카운터가 초기화되지 않음)
  
  Turn 10: 세션 만료 재시도 → rate-limit 재시도 → 교차 오염
```

**문제**: 세션 만료 복구와 rate-limit 재시도가 얽히면서 **예측 불가능한 동작**.

---

## 4️⃣ Main 루프와의 상태 불일치

### 문제 코드 (run.py:1618-1622)

```python
else:  # "failed"
    state_record_fail(state, step)
    log.error(f"[{step:03d}] 최종 실패. 중단합니다.")
    log.error(f"  재개 명령: python3 run.py --resume")
    sys.exit(1)  # ❌ 즉시 종료
```

### 문제점

1. **Rate-limit은 일시적 오류인데 "최종 실패"로 처리**
   - Rate-limit 초과(30회) → `run_with_retry()` returns `("failed", ...)`
   - `main()`은 이를 `"failed"` 판정 → `sys.exit(1)` 즉시 종료

2. **상태 저장 시점 애매함**
   - `state_record_fail(state, step)` 호출 → `failed` 배열에 기록
   - 하지만 rate-limit은 실패가 아니라 **시간 초과**
   - 재개 시 같은 스텝에서 다시 시작하는데, **rate-limit 상태 정보 없음**

3. **사용자 기대와의 괴리**
   - 메시지: "재개 명령: python3 run.py --resume"
   - 하지만 rate-limit이 아직 살아있을 가능성 높음
   - 재개 후 동일한 rate-limit 오류 반복 가능

---

## 5️⃣ 주석 설명과 실제 코드 로직 불일치

### 문제 코드 (run.py:1044-1046)

```python
if verdict == "suspicious":
    # 조용한 실패: 재시도 없이 즉시 반환 (메인 루프에서 사용자에게 판단 요청)
    return ("suspicious", sid, total_duration)
```

**주석 문제**: "메인 루프에서 사용자에게 판단 요청"한다고 했지만, 실제로는 그렇지 않음.

메인 루프 확인:

```python
elif verdict == "suspicious":
    # ... 자동 건너뜀 (사용자 판단 요청 없음)
    state_record_complete(state, step)
```

---

## 🔍 실제 장애 시나리오 재현

### 위 5가지 결합 시 최악의 경우

```
Step 35 실행 중:
┌─────────────────────────────────────────────────────┐
│ Turn 1-5: Rate-limit (rate_limit_retries=5)        │
│ Turn 6: "try again later" 감지 → 또 rate-limit!    │
│ ...                                                  │
│ Turn 30: rate_limit_retries=30 도달                │
│   → rate_limit_retries > MAX 체크                   │
│   → run_with_retry() breaks and returns ("failed")  │
└─────────────────────────────────────────────────────┘

Main Loop:
┌─────────────────────────────────────────────────────┐
│ verdict == "failed"                                 │
│ → state_record_fail(state, 35)  (상태 오염)        │
│ → log: "[035] 최종 실패. 중단합니다."              │
│ → sys.exit(1)  (강제 종료)                         │
└─────────────────────────────────────────────────────┘

--resume 재개 시:
┌─────────────────────────────────────────────────────┐
│ state.json에서 current_step=36 읽음                 │
│ (Step 35는 이미 failed로 표시됨)                   │
│ 문제: Step 35를 재시도해야 하는데도                │
│       건너뛰고 Step 36부터 시작 가능성!            │
└─────────────────────────────────────────────────────┘
```

---

## 📊 결과 요약

| 문제 | 심각도 | 영향 |
|------|--------|------|
| **주석-코드 불일치** | 🟡 중 | 사용자 혼동, 예측 불가능 |
| **Rate-limit 검출 오판** | 🔴 높 | 불필요한 시간 낭비 (수시간) |
| **카운터 상태 관리** | 🔴 높 | 의도치 않은 루프 탈출 |
| **Main과의 상태 불일치** | 🔴 높 | 실행 중단, --resume 재개 실패 |
| **세션 만료 + Rate-limit 교차** | 🟠 매우 높 | 예측 불가능한 동작 |

---

## ✅ 권장 수정안 (별도 문서)

다음 문서 참조: `RATELIMIT_FIX_RECOMMENDATIONS.md`

### 즉시 적용 가능한 핸드팩스

1. **Rate-limit 검출 키워드 정확화**
   - `"try again"`, `"too many"` 제거
   - rate-limit 전용 패턴만 유지

2. **MAX_RATE_LIMIT_RETRIES 증량**
   - 60회로 복구 (5시간 유지)
   - 주석과 코드 일치

3. **상태 관리 개선**
   - rate-limit 중단을 "failed"가 아닌 별도 상태로 분류
   - state.json에 rate-limit 재시도 횟수 기록

4. **세션 + Rate-limit 상태 분리**
   - 두 상태 기계가 독립적으로 작동하도록 리팩토링
