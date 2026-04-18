EVAL_CASES = [
    {
        "name": "program_deadline_and_headcount",
        "query": "AI 윤리 워크숍 신청 마감일과 참여 인원 알려줘.",
        "expected_source": "demo_programs.txt",
        "expected_substrings": ["2026-05-08", "총 40명"],
    },
    {
        "name": "program_compare",
        "query": "AI 윤리 워크숍과 스마트캠퍼스 세미나의 장소와 일정을 각각 알려줘.",
        "expected_source": "demo_programs.txt",
        "expected_substrings": [
            "AI 윤리 워크숍",
            "인문관 201호",
            "2026-05-12 14:00~17:00",
            "스마트캠퍼스 세미나",
            "소프트웨어관 301호",
            "2026-06-03 10:00~16:00",
        ],
    },
    {
        "name": "table_row_generalization",
        "query": "모니터링단 대상 교과목에서 건강유토피아의 교과목번호와 교강사명 알려줘.",
        "expected_source": "(붙임2) 모니터링단_대상교과목(2026-1).pdf",
        "expected_substrings": ["521810"],
    },
    {
        "name": "volunteer_numbers_and_fee",
        "query": "몽골 해외봉사에서 총 인원, 학생 선발 인원, 개인 부담금을 각각 알려줘.",
        "expected_source": "demo_global_volunteer.txt",
        "expected_substrings": ["30명", "26명", "600000원"],
    },
    {
        "name": "program_summary",
        "query": "학생 지원 프로그램 안내 문서를 3문장으로 요약해줘.",
        "expected_source": "demo_programs.txt",
        "expected_substrings": ["AI 윤리 워크숍", "스마트캠퍼스 세미나"],
    },
]
