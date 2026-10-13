# -*- coding: utf-8 -*-
"""[RECOMMEND] 추천 단계: 저장된 공고(ELIG_JSON)와 학생 프로필(개인정보 제외)을 비교한다.

  profile.py  학생 프로필 검증·정규화 (이름/학번/연락처/상세 주소 등은 받지 않음)
  vocab.py    공고의 표현 <-> 프로필 값 대응 (학적 상태, 지역명, 소득 유형 ...)
  matcher.py  조건별 판정 (충족/미달/알 수 없음)과 variants(유형별 조건) 해석
  dedupe.py   같은 장학의 원 공고/연장 공고 묶기
  service.py  후보 조회 -> 묶기 -> 판정 -> 정렬/경고 -> 결과(JSON)
  file_source.py  DB 없이 --dump-dir 의 ai_result.json 으로 시험하기 위한 후보 공급원

AI 는 호출하지 않는다 (코드 판정만). 문장으로만 적힌 조건은 '확인 필요' 항목으로 돌려준다.
"""
