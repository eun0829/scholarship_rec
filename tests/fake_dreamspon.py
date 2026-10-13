# -*- coding: utf-8 -*-
"""드림스폰 흉내 서버 (테스트 전용). 실제 사이트의 요청 형식(로그인 JSON, 쿠키 세션, POST 다운로드)을 따른다."""
import html as htmllib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOGIN_FIELDS = {"mode", "userid", "idsaveCheck", "autoLogin", "pageReferer", "userpw"}
LOGIN_PAGE = "<html><head><title>로그인, 드림스폰</title></head><body><form id='loginForm'><input name='mbr_id'></form></body></html>"


def list_html(rows, max_page=1):
    """rows: (idx, title, org, tags, dday, state). 실제 목록 화면과 같은 class 구조."""
    trs = []
    for idx, title, org, tags, dday, state in rows:
        trs.append(
            "<tr class=''><td class='td_subject'><p class='title'><a href='/scholarship/view.html?idx={}'>{}</a></p>"
            "<div class='hashtag'>{}</div></td><td>{}</td><td class='td_day'><span class='count'><span>D-{}</span></span>"
            "<span class='state bgBlue'>{}</span></td><td class='hit'>10</td></tr>".format(
                idx, htmllib.escape(title), "".join("<span>#{}</span>".format(t) for t in tags), htmllib.escape(org), dday, state))
    pages = "".join("<a href='?page={}'>{}</a>".format(i, i) for i in range(2, max_page + 1))
    return ("<html><body><table><thead><tr><th>장학명</th></tr></thead><tbody>{}</tbody></table><div class='paging'>{}</div></body></html>"
            .format("".join(trs), pages))


def sections_html(sections):
    out = []
    for heading, lines in sections.items():
        out.append("<p><span style='font-size: 14pt'><strong>• {}</strong></span></p>".format(heading))
        out.extend("<p>{}</p>".format(htmllib.escape(x)) for x in lines)
        out.append("<br/><br/>")
    return "".join(out)


DEFAULT_SECTIONS = {
    "신청기간": ["2026. 10. 01. ~ 2026. 10. 31."], "선발인원": ["총 5명"], "장학혜택": ["[장학금지급] 최대 100 만원"],
    "접수방법": ["온라인 접수", "- 재단 홈페이지에서 신청"], "지원대상": ["대학생"],
    "신청자격": ["[지역기준] 거주지", "- 서울 거주 대학생", "[성적기준] 대학 학점", "- 직전학기 평점 3.0/4.5 이상"],
    "장학금 문의": ["- 문의처 : 테스트재단", "- 전화 : 02-000-0000"]}


def detail_html(idx, title="테스트 장학", org="테스트재단", sections=None, info_period="2026. 10. 01. (목) ~ 2026. 10. 31. (토)",
                files=(), original="https://example.org/notice/1", dday=3):
    sections = DEFAULT_SECTIONS if sections is None else sections
    fl = "".join("<span><a class='btn_file_down' data-filepath='{}' href='#'>{}</a></span>".format(htmllib.escape(fp), htmllib.escape(nm))
                 for nm, fp in files)
    return (
        "<html><head><title>{t}, 드림스폰</title></head><body>"
        "<div class='v_hd'><div class='subject'>{t}</div><div class='name'>{o}</div><div class='hit'>조회 : 7</div>"
        "<div class='opt-bx'><div class='button state'><span>D-{d}</span></div></div></div>"
        "<div class='infoTable basic-info'>"
        "<ul><li class='col'>장학종류</li><li> 장학금 </li></ul><ul><li class='col'>선발인원</li><li> 총 5명 선발 </li></ul>"
        "<div class='ul-wr'><ul class='ul-file'><li class='col'>첨부파일</li><li><div class='pop-file-bx'><div class='body'>"
        "<p class='t1'>첨부파일 {n}개 (1KB) <a class='btn_file_all' href='#'>모두저장</a></p><p class='file-list'>{fl}</p></div></div>"
        "<form id='fForm'><input id='file1' name='file1' type='hidden' value=''/><input name='idx' type='hidden' value='{i}'/></form></li></ul>"
        "<ul><li class='col'>원문공고</li><li><a class='button' href=\"javascript:goScholarship('O', '{u}', {i})\">공고 바로가기</a></li></ul></div>"
        "<ul><li class='col'>신청기간</li><li class='day'><p>{ip}</p></li></ul></div>"
        "<div id='t_content'><div class='contbox' id='tab1s'><dl class='scholarship04'><dt></dt><dd>{s}</dd></dl></div>"
        "<div class='contbox'><dl><dt>· 기관명</dt><dd>{o}</dd></dl></div></div>"
        "<div id='con1'><input value='user@example.com'></div></body></html>"
    ).format(t=htmllib.escape(title), o=htmllib.escape(org), d=dday, n=len(files), fl=fl, i=idx, u=original, ip=info_period,
             s=sections_html(sections))


class FakeDreamspon:
    def __init__(self, uid="student@example.com", pw="Pa$$w0rd!", list_pages=None, details=None, files=None):
        self.uid, self.pw = uid, pw
        self.list_pages = list_pages or {}          # (ordby or 0, page) -> html
        self.details = details or {}                # idx -> html
        self.files = files or {}                    # file1 -> bytes
        self.requests = []                          # (method, path, params/form, logged_in)
        self.sessions = set()
        self.login_calls = 0
        self.list_failures = 0                      # 앞으로 N번의 목록 요청은 500
        self.expire_after_details = None            # N번째 상세 이후 세션 만료
        self._detail_hits = 0
        self.last_login_form = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def _send(self, status, body, ctype="text/html; charset=utf-8", extra=None):
                data = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _logged_in(self):
                for part in (self.headers.get("Cookie") or "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "sid" and v in outer.sessions:
                        return True
                return False

            def _form(self):
                n = int(self.headers.get("Content-Length") or 0)
                return {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True).items()}

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                outer.requests.append(("GET", u.path, q, self._logged_in()))
                if u.path == "/scholarship/list.html":
                    if outer.list_failures > 0:
                        outer.list_failures -= 1
                        return self._send(500, "error")
                    page = outer.list_pages.get((int(q.get("ordby", 0)), int(q.get("page", 1))))
                    return self._send(200, page if page is not None else list_html([]))
                if u.path == "/scholarship/view.html":
                    outer._detail_hits += 1
                    if outer.expire_after_details and outer._detail_hits > outer.expire_after_details and not getattr(outer, "_expired", False):
                        outer._expired = True
                        outer.sessions.clear()
                    if not self._logged_in():
                        return self._send(200, LOGIN_PAGE)
                    body = outer.details.get(q.get("idx"))
                    return self._send(200, body if body is not None else LOGIN_PAGE)
                if u.path.startswith("/data/"):
                    return self._send(200, b"\x89PNG-fake", "image/png")
                self._send(404, "not found")

            def do_POST(self):
                u = urlparse(self.path)
                form = self._form()
                outer.requests.append(("POST", u.path, form, self._logged_in()))
                if u.path == "/process/checkuser.html":
                    outer.login_calls += 1
                    outer.last_login_form = form
                    if set(form) == LOGIN_FIELDS and form["mode"] == "login" and form["userid"] == outer.uid and form["userpw"] == outer.pw:
                        sid = "sess{}".format(outer.login_calls)
                        outer.sessions.add(sid)
                        return self._send(200, json.dumps({"result": True, "checkyn": "Y", "pageLink": "/"}), "application/json",
                                          {"Set-Cookie": "sid={}; Path=/".format(sid)})
                    return self._send(200, json.dumps({"result": True, "checkyn": "N"}), "application/json")
                if u.path == "/process/nonjson.html":                      # 200 이지만 JSON 이 아닌 응답 (사이트 화면 변경 시뮬레이션)
                    return self._send(200, "<html><body>점검 중입니다</body></html>")
                if u.path == "/process/fileDown.html":
                    if not self._logged_in():
                        return self._send(200, LOGIN_PAGE)
                    data = outer.files.get(form.get("file1"))
                    if data is None or "idx" not in form:
                        return self._send(404, "no file")
                    return self._send(200, data, "application/octet-stream")
                self._send(404, "not found")

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = "http://127.0.0.1:{}".format(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
