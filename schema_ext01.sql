-- ============================================================
-- 외부 장학금 공고 저장 DDL (외부01 = 학교 장학공지 / 외부02 = 드림스폰 공통)
-- Oracle 11g 호환
--   * IDENTITY 컬럼 미사용 (12c 이상) -> 원본 글번호/첨부번호를 키로 사용
--   * JSON 데이터타입, IS JSON 제약, JSON_VALUE 미사용 (12.1.0.2 이상) -> JSON은 CLOB로 저장
--   * 한글 길이를 글자 단위로 잡기 위해 VARCHAR2(n CHAR) 사용
--   * SQL 필터에 쓰는 값은 마감일/구분/상태 컬럼만 둠
--     (학교급, 지역 등 나머지 하드 조건은 ELIG_JSON을 파싱해 애플리케이션에서 판정)
-- ============================================================

-- ------------------------------------------------------------
-- 1) 공고 (본문 + AI 구조화 결과)
-- ------------------------------------------------------------
CREATE TABLE SCHOLARSHIP_NOTICE (
    SRC_CD          VARCHAR2(10)      NOT NULL,                 -- 출처: EXT01(학교 장학공지), EXT02(드림스폰)
    SRC_ID          VARCHAR2(30)      NOT NULL,                 -- 원본 글번호 (EXT01: bbs_artcl_seq)
    TITLE           VARCHAR2(500 CHAR),                         -- 제목
    DEPT_NM         VARCHAR2(100 CHAR),                         -- 작성 부서 (ANNYMTY_WRITER)
    POSTED_DATE     DATE,                                       -- 게시일 (rgsde)
    ORIGIN_URL      VARCHAR2(1000),                             -- 원문 링크 (학생 화면 노출용)
    BODY_TEXT       CLOB,                                       -- 정리된 본문 텍스트
    CATEGORY_CD     VARCHAR2(20),                               -- OUT(교외) / IN(교내) / NATIONAL(국가) / LOAN(대출·이자) / INFO(안내) / ETC
    APPLY_TYPE_CD   VARCHAR2(20),                               -- SCHOOL(학교추천) / DIRECT(직접신청) / UNKNOWN
    APPLY_START     DATE,                                       -- 신청 시작일
    APPLY_END       DATE,                                       -- 학생에게 보여줄 마감 (학교 제출 기한)
    ORG_DEADLINE    DATE,                                       -- 재단 제출 기한 (학교 기한과 다른 경우)
    ELIG_JSON       CLOB,                                       -- 자격요건 JSON (AI 추출 결과)
    UNCERTAIN_JSON  CLOB,                                       -- 불확실 항목, 근거 출처 JSON
    SUMMARY         VARCHAR2(1000 CHAR),                        -- 한 줄 요약
    CONTENT_HASH    VARCHAR2(64),                               -- 본문+첨부 내용 해시 (수정 감지)
    STATUS_CD       VARCHAR2(20)      DEFAULT 'COLLECTED' NOT NULL,  -- COLLECTED / EXTRACTED / NEED_REVIEW / FAILED
    EXTRACT_MODEL   VARCHAR2(50),                               -- 추출에 사용한 AI 모델
    EXTRACTED_AT    DATE,                                       -- AI 추출 시각
    USE_YN          CHAR(1)           DEFAULT 'Y' NOT NULL,     -- 추천 대상 여부 (삭제/비공개/제외 시 N)
    CRAWLED_AT      DATE              DEFAULT SYSDATE NOT NULL, -- 최초 수집 시각
    UPDATED_AT      DATE              DEFAULT SYSDATE NOT NULL, -- 마지막 갱신 시각 (애플리케이션에서 갱신)
    CONSTRAINT PK_SCHOLARSHIP_NOTICE PRIMARY KEY (SRC_CD, SRC_ID),
    CONSTRAINT CK_SCH_NOTICE_USE CHECK (USE_YN IN ('Y', 'N'))
);

COMMENT ON TABLE  SCHOLARSHIP_NOTICE                IS '외부 장학금 공고 (본문 및 AI 구조화 결과)';
COMMENT ON COLUMN SCHOLARSHIP_NOTICE.APPLY_END      IS '학생에게 보여줄 마감일 (학교 제출 기한 기준)';
COMMENT ON COLUMN SCHOLARSHIP_NOTICE.ELIG_JSON      IS '자격요건 JSON (CLOB). 11g에서는 JSON 검증/조회 함수가 없어 앱에서 검증';
COMMENT ON COLUMN SCHOLARSHIP_NOTICE.STATUS_CD      IS 'COLLECTED=수집됨, EXTRACTED=추출완료, NEED_REVIEW=확인필요, FAILED=실패';

CREATE INDEX IDX_SCH_NOTICE_FILTER ON SCHOLARSHIP_NOTICE (USE_YN, CATEGORY_CD, APPLY_END);
CREATE INDEX IDX_SCH_NOTICE_POSTED ON SCHOLARSHIP_NOTICE (POSTED_DATE);

-- ------------------------------------------------------------
-- 2) 첨부파일 (다운로드 + 텍스트 추출 결과)
-- ------------------------------------------------------------
CREATE TABLE SCHOLARSHIP_NOTICE_FILE (
    SRC_CD          VARCHAR2(10)      NOT NULL,
    SRC_ID          VARCHAR2(30)      NOT NULL,
    FILE_SEQ        VARCHAR2(30)      NOT NULL,                 -- 원본 첨부번호 (EXT01: BBS_ATCHMNFL_SEQ)
    SORT_NO         NUMBER(5),                                  -- 첨부 순서
    ORG_FILE_NM     VARCHAR2(500 CHAR),                         -- 원본 파일명
    FILE_EXT        VARCHAR2(10),                               -- 확장자 (hwp, pdf, jpg ...)
    FILE_SIZE       NUMBER(12),                                 -- 바이트
    NAS_PATH        VARCHAR2(1000),                             -- NAS 저장 경로
    ROLE_CD         VARCHAR2(20),                               -- GUIDE(요강) / FORM(양식) / AUX(보조) / EXCLUDED(제외) / UNKNOWN(미분류)
    EXTRACT_METHOD  VARCHAR2(20),                               -- TEXT / VISION / OCR / NONE
    EXTRACT_TEXT    CLOB,                                       -- 추출된 텍스트
    TEXT_LEN        NUMBER(10),                                 -- 추출 글자 수 (이상 감지용)
    STATUS_CD       VARCHAR2(20)      DEFAULT 'NEW' NOT NULL,   -- NEW / DOWNLOADED / EXTRACTED / FAILED / SKIPPED
    ERR_MSG         VARCHAR2(1000 CHAR),                        -- 실패 사유
    CRAWLED_AT      DATE              DEFAULT SYSDATE NOT NULL,
    UPDATED_AT      DATE              DEFAULT SYSDATE NOT NULL,
    CONSTRAINT PK_SCHOLARSHIP_NTC_FILE PRIMARY KEY (SRC_CD, SRC_ID, FILE_SEQ),
    CONSTRAINT FK_SCH_NTC_FILE FOREIGN KEY (SRC_CD, SRC_ID)
        REFERENCES SCHOLARSHIP_NOTICE (SRC_CD, SRC_ID)
);

CREATE INDEX IDX_SCH_NTC_FILE_STATUS ON SCHOLARSHIP_NOTICE_FILE (STATUS_CD);

-- ============================================================
-- 추천 후보 1차 조회 예시
--   * SQL에서는 마감/구분/상태만 거르고, 학교급/지역 등은 ELIG_JSON을 파싱해 앱에서 판정
--   * APPLY_END가 NULL인 공고(마감일 불명)를 포함할지는 정책 결정 필요
--     (아래 예시는 마감일이 있는 공고만 조회)
-- ============================================================
-- SELECT n.SRC_CD, n.SRC_ID, n.TITLE, n.APPLY_END, n.SUMMARY, n.ELIG_JSON
-- FROM   SCHOLARSHIP_NOTICE n
-- WHERE  n.USE_YN = 'Y'
-- AND    n.CATEGORY_CD = 'OUT'
-- AND    n.STATUS_CD IN ('EXTRACTED', 'NEED_REVIEW')
-- AND    n.APPLY_END >= TRUNC(SYSDATE)
-- ORDER BY n.APPLY_END;
