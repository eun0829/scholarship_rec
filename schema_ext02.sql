-- ============================================================
-- 외부02(드림스폰) 추가분 DDL (Oracle 11g)  - 이미 적용했다면 다시 실행하지 않아도 됩니다.
-- 출처가 제공한 구조화 항목(신청기간/선발인원/장학혜택/접수방법/지원대상/신청자격/장학금 문의, 기관정보, 원문 URL)을 JSON 으로 저장.
-- AI 해석(ELIG_JSON)과 분리해 두어, 파서/프롬프트를 개선해도 다시 크롤링하지 않고 재처리할 수 있습니다. 외부01 행은 NULL.
-- ============================================================
ALTER TABLE SCHOLARSHIP_NOTICE ADD (SRC_FIELDS_JSON CLOB);
COMMENT ON COLUMN SCHOLARSHIP_NOTICE.SRC_FIELDS_JSON IS '출처가 제공한 구조화 항목 JSON (EXT02: period/headcount/benefit/method/target/eligibility/contact/org/original_url/list)';
