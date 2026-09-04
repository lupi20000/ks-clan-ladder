# -*- coding: utf-8 -*-
"""
ks clan 래더 순위 갱신기

하는 일
  1) cwal.gg 전체 래더(상위 2만명)를 통째로 받아온다
  2) 거기서 [kS] 태그가 붙은 계정을 전부 골라낸다
  3) 명단.json 의 클랜원과 짝을 맞춘다
  4) 짝이 안 맞은 사람은 개별 조회로 한 번 더 찾는다
  5) 계정 이름에 [kS] 가 붙어 있고 클랜티어가 적힌 사람만 남긴다
  6) 남은 계정의 종족별 전적과 최근 10전을 받아온다
  7) 결과를 data.js 로 저장한다

순위에 올리는 기준
  이름만 비슷한 남을 잘못 올리는 일이 없도록, 두 가지를 모두 만족할 때만 올린다.
    - 계정 이름에 [kS] 클랜 태그가 붙어 있을 것
    - 명단에 클랜티어가 적혀 있을 것
  여기서 빠진 사람은 순위표 위쪽 '래더 아이디 추가' 칸에 본인이 적어 넘기면 된다.

쓰는 법 : 이 파일을 더블클릭하거나  python 갱신.py
          끝나면 index.html 을 열면 최신 순위가 보인다.

짝 맞추는 순서 (앞쪽일수록 확실함)
  1. 명단의 래더아이디 칸       -> 확정
  2. 클랜ID[kS] 형태의 계정     -> 확정 (클랜 태그가 붙어 있으니 본인)
  3. 배틀코드 이름이 래더 전체에서 유일 -> 확정
  4. 클랜ID 와 똑같은 계정      -> 추정 (동명이인일 수 있음)

빠르게 도는 이유
  cwal.gg 에 한 번에 하나씩 물어보면 몇십 분이 걸린다.
  그래서 WORKERS 개씩 동시에 물어본다. 1분 안팎이면 끝난다.
  더 올려도 되지만 남의 서버라 너무 세게 두들기지는 않는다.

주의: 404(계정 없음)와 통신오류를 반드시 구분한다.
      오류를 '없음'으로 처리하면 멀쩡한 클랜원이 순위에서 통째로 빠진다.
"""
import io, os, sys, re, csv, json, time, datetime, hashlib
import concurrent.futures as cf
import urllib.parse, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)

BASE = 'https://api.aws.cwal.gg/api'
UA = {'User-Agent': 'Mozilla/5.0', 'Origin': 'http://localhost:5173'}
PAGES = 200                      # 100명씩 200페이지 = 상위 2만명 (API 상한)
GATEWAYS = (30, 20)              # 30=한국 20=아시아
WORKERS = 8                      # cwal.gg 에 동시에 물어보는 개수
ROSTER, OUT = '명단.json', 'data.js'

# 순위표의 [등록요청] 버튼이 보낸 내용이 쌓이는 구글시트 (구글폼 응답)
# 시트가 '링크가 있는 모든 사용자 - 뷰어' 로 열려 있어야 로그인 없이 읽힌다
REQ_CSV = ('https://docs.google.com/spreadsheets/d/'
           '1IsIFpcGqrZ56pWUxMBfWuTHEtZAwr5bWhcbjHSWn9kU/gviz/tq?tqx=out:csv'
           '&sheet=%EC%84%A4%EB%AC%B8%EC%A7%80%20%EC%9D%91%EB%8B%B5%20%EC%8B%9C%ED%8A%B81')
# 관리자 모드(admin.html)에서 넣은 "계정 추가 / 빼기" 가 쌓이는 곳.
# 클라우드플레어 중계소가 보관하고 있고, 여기서는 읽기만 한다.
ADMIN_OPS = 'https://ks-replay.lupi20000.workers.dev/admin/ops'
TPL = '공유용틀.html'          # 순위표 화면의 원본 틀
LOCAL = 'index.html'           # 내 컴퓨터에서 보는 판 (data.js 를 옆에서 읽음)
SHARE = '공유용.html'          # 파일 하나로 다 들어있는 판 (보내거나 웹에 올릴 때)
MARK = '<!--__KS_DATA__-->'    # 틀에서 데이터가 들어갈 자리
# 깃허브에 올라가는 판 (인터넷에 그대로 공개됨)
# 깃허브에서 자동으로 돌 때는 KS_PUB=index.html 로 바꿔 쓴다
PUB = os.environ.get('KS_PUB') or '공개/index.html'

# 공개용에서 빼는 항목 — 클랜 내부에서만 볼 것들
DROP_TOP = ('missing', 'errors', 'total', 'ladderSize')
DROP_ROW = ('auroraId', 'how', 'sure', 'alts', 'id', 'race')

# 관리자 화면이 "지금 순위에 있는 계정" 을 보여줄 때 읽는 파일.
# 공개 페이지와 같은 폴더에 둔다.
ACC = os.path.join(os.path.dirname(PUB), '계정.json')

# 등록요청이 들어왔는데 래더에서 계정을 못 찾은 목록.
# 카페에 안내글을 올리는 봇이 이 파일만 읽어간다.
NF = os.path.join(os.path.dirname(PUB), 'notfound.json')


def page(tpl, inner):
    """틀에 데이터를 끼워 온전한 html 파일 한 장으로 만든다"""
    return ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<title>ks clan 래더 순위</title>\n</head>\n<body>\n'
            + tpl.replace(MARK, inner) + '\n</body>\n</html>\n')

# 리플레이 파일 주소는 이 뒤에 붙는 부분만 다르다. 페이지에는 뒷부분만 넣는다.
RP = 'https://storage.googleapis.com/starcraft-user-uploads-prod/S1-replays/'

KS = re.compile(r'\[\s*k\s*s\s*[\]\)]', re.I)     # [kS] [ks) [KS] …
TIERS = ('Black', 'Gold', 'Yellow', 'Red', 'Violet', 'Blue', 'Sky', 'White')


def fetch(url, tries=4):
    """성공하면 dict / 확실히 없으면 'none' / 끝까지 실패하면 'error'"""
    for t in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 'none'
            time.sleep(1.5 * (t + 1))
        except Exception:
            time.sleep(1.5 * (t + 1))
    return 'error'


def each(fn, items, label='', step=50):
    """여러 개를 동시에 물어본다. 결과는 넣은 순서 그대로 돌려준다."""
    out = [None] * len(items)
    if not items:
        return out
    done = [0]
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
        for f in cf.as_completed(futs):
            try:
                out[futs[f]] = f.result()
            except Exception:
                out[futs[f]] = None
            done[0] += 1
            if label and done[0] % step == 0:
                print('  ...%d/%d %s' % (done[0], len(items), label))
    return out


def sweep():
    """전체 래더를 훑어 계정 목록을 만든다."""
    print('전체 래더 수집 중 (2만명)')
    pages = each(lambda p: fetch('%s/leaderboard?page=%d&pageSize=100' % (BASE, p)),
                 list(range(1, PAGES + 1)), '페이지')
    accounts, failed = {}, 0
    for d in pages:
        if not isinstance(d, dict):
            failed += 1
            continue
        for x in d.get('rows', []):
            accounts[(x['gateway'], x['toon'])] = x
    if failed:
        print('  (페이지 %d개는 못 받았습니다)' % failed)
    return list(accounts.values())


def row_of(r, acc, how, sure):
    """순위표 한 줄 만들기"""
    return {
        'estMmr': False,          # 점수를 경기 기록에서 되살렸는지
        'id': r['id'] if r else None,
        'tier': r['tier'] if r else '',
        'race': r['race'] if r else '',
        'toon': acc['toon'], 'gateway': acc['gateway'],
        'auroraId': acc.get('auroraId'),
        'rating': acc.get('rating') or 0,
        'standing': acc.get('standing') or acc.get('viewRank'),
        'cwalRace': acc.get('race') or (r['race'] if r else ''),
        'wins': acc.get('wins') or 0, 'losses': acc.get('losses') or 0,
        'battleTag': acc.get('battleTag') or '',
        'how': how, 'sure': bool(sure), 'alts': [],
    }


def detail(gw, toon, n=10):
    """계정 하나의 종족별 전적과 최근 n경기를 받아온다.

    순위표에서 계정 이름을 눌렀을 때 펼쳐지는 내용이다.
    실패해도 그냥 비워둔다 (순위 자체에는 지장이 없다).

    MMR 도 같이 계산해서 돌려준다. cwal 이 어떤 계정은 점수를 0 으로 주는데,
    경기 기록에는 그때그때 점수가 남아 있어서 마지막 경기로 되살릴 수 있다.
    """
    t = urllib.parse.quote(toon, safe='')
    mu, recent, mmr = {}, [], 0

    d = fetch('%s/player/%d/%s' % (BASE, gw, t))
    if isinstance(d, dict):
        st = (d.get('profile') or {}).get('stats') or {}
        for k, v in (st.get('byMatchup') or {}).items():
            mu[k] = [v.get('wins') or 0, v.get('losses') or 0]

    d = fetch('%s/player/%d/%s/matches' % (BASE, gw, t))
    if isinstance(d, dict):
        ms = d.get('matches') or []
        if ms and ms[0].get('mmr'):
            # 목록 맨 앞이 가장 최근 경기다. 그때 점수 + 증감 = 지금 점수
            mmr = (ms[0].get('mmr') or 0) + (ms[0].get('mmrDelta') or 0)
        for m in ms[:n]:
            recent.append({
                'w': 1 if m.get('result') == 'win' else 0,   # 이겼나
                'm': m.get('matchup') or '',                 # TvZ 같은 종족 대결
                'o': m.get('opponentToon') or '',            # 상대 계정
                'd': m.get('mmrDelta') or 0,                 # 점수 증감
                'i': m.get('matchId') or '',                 # 리플레이 찾을 때 쓰는 경기번호
                't': m.get('timestamp') or 0,                # 경기한 시각 (1970년부터 밀리초)
            })
    return mu, recent, mmr


def replays(rows):
    """최근 경기들의 리플레이 파일 주소를 받아 각 경기에 붙인다.

    cwal 은 경기번호로 물어보면 리플레이 파일이 있는 주소를 알려준다.
    같은 경기가 두 클랜원한테 겹쳐 나오므로 번호를 겹치지 않게 모아서 한 번씩만 묻는다.
    주소는 앞부분이 늘 똑같아서 뒷부분만 저장한다 (페이지 용량을 아끼려고).
    """
    ids = []
    seen = set()
    for r in rows:
        for m in r.get('recent') or []:
            i = m.get('i')
            if i and i not in seen:
                seen.add(i)
                ids.append(i)
    if not ids:
        return

    print('리플레이 주소 받는 중 (%d경기)' % len(ids))
    got = {}

    def ask(todo):
        for i, d in zip(todo, each(lambda x: fetch('%s/replay/%s' % (BASE, x)),
                                   todo, '경기', 200)):
            if isinstance(d, dict) and d.get('replayUrl'):
                u = d['replayUrl']
                got[i] = u[len(RP):] if u.startswith(RP) else u

    ask(ids)
    # 한꺼번에 물어보면 일부는 빈손으로 온다. 못 받은 것만 골라 한 번 더 묻는다.
    again = [i for i in ids if i not in got]
    if again:
        print('  못 받은 %d경기 다시 물어보는 중' % len(again))
        time.sleep(3)
        ask(again)

    for r in rows:
        for m in r.get('recent') or []:
            u = got.get(m.pop('i', None))
            if u:
                m['rp'] = u                # 리플레이 내려받기 주소
    print('  리플레이 찾음 %d / %d 경기' % (len(got), len(ids)))


def lookup(toon, gateways=GATEWAYS):
    """개별 조회. (계정, 오류여부) 반환"""
    err = False
    for gw in gateways:
        d = fetch('%s/player/%d/%s' % (BASE, gw, urllib.parse.quote(toon, safe='')))
        if d == 'error':
            err = True
            continue
        if d == 'none' or not isinstance(d, dict) or not d.get('profile'):
            continue
        p, rk = d['profile'], (d.get('rank') or {})
        acc = dict(p)
        acc['standing'] = rk.get('standing')
        acc['race'] = rk.get('race') or p.get('race')
        return acc, err
    return None, err


def inbox():
    """순위표에서 [등록요청] 으로 들어온 줄들을 읽어온다.

    구글폼 응답 시트를 표(csv)로 받아온다. 칸 순서는
    타임스탬프 / 클랜ID / 래더계정 / 클랜티어 이다.
    시트를 못 읽어도 갱신은 그대로 진행한다 (요청만 이번에 안 들어갈 뿐).
    """
    try:
        req = urllib.request.Request(REQ_CSV, headers={'User-Agent': UA['User-Agent']})
        raw = urllib.request.urlopen(req, timeout=30).read().decode('utf-8')
    except Exception as e:
        print('등록요청 시트를 못 읽었습니다 (%s) — 이번엔 건너뜁니다' % e)
        return []

    out = []
    for row in csv.reader(io.StringIO(raw)):
        if len(row) < 4:
            continue
        if row[0].strip() in ('', '타임스탬프', 'Timestamp'):
            continue                       # 제목줄
        cid, toon, tier = row[1].strip(), row[2].strip(), row[3].strip()
        base = toon.split('[')[0].strip()      # 태그를 뗀 계정 이름
        if not base:
            continue                           # 빈 줄이거나 '[kS]' 만 적은 장난 줄
        out.append({'id': cid or base, 'ladderId': toon, 'tier': tier})
    return out


def 안전글자(s, n=40):
    """카페 안내글에 끼워 넣을 값을 안전하게 다듬는다.

    등록요청 칸은 누구나 아무 글이나 적어 보낼 수 있는 곳이다.
    줄바꿈이나 이상한 글자가 섞여 들어오면 안내글 모양이 망가지거나
    엉뚱한 문장이 끼어들 수 있으니, 눈에 보이는 글자만 남기고 길이도 자른다.
    """
    s = ''.join(c for c in str(s or '') if c.isprintable())
    return re.sub(r'\s+', ' ', s).strip()[:n]


def 못찾은요청(reqs, rows):
    """등록요청이 들어왔는데 순위에 못 올라간 것만 골라낸다."""
    있는계정 = set((r.get('toon') or '').lower() for r in rows)
    있는아이디 = set((r.get('id') or '').lower() for r in rows if r.get('id'))
    out, 봤다 = [], set()
    for q in reqs:
        toon = (q.get('ladderId') or '').strip()
        cid = (q.get('id') or '').strip()
        if toon.lower() in 있는계정 or (cid and cid.lower() in 있는아이디):
            continue                       # 순위에 잘 올라갔다
        key = (cid.lower(), toon.lower())
        if key in 봤다:
            continue                       # 같은 요청이 두 번 들어온 경우
        봤다.add(key)
        out.append({'clanId': 안전글자(cid), 'ladderId': 안전글자(toon)})
    return out


def admin():
    """관리자 모드에서 넣어둔 '계정 추가 / 빼기' 를 읽어온다.

    돌려주는 것은 세 가지다.
      adds  : 명단에 합칠 줄들 (등록요청과 같은 모양)
      force : [kS] 태그가 없어도 순위에 올릴 계정 이름들
      drop  : 순위에서 뺄 계정 이름들
    중계소를 못 읽어도 갱신은 그대로 진행한다.
    """
    try:
        req = urllib.request.Request(ADMIN_OPS, headers={'User-Agent': UA['User-Agent']})
        got = json.loads(urllib.request.urlopen(req, timeout=20).read().decode('utf-8'))
        ops = got.get('ops') or []
    except Exception as e:
        print('관리자 변경목록을 못 읽었습니다 (%s) — 이번엔 건너뜁니다' % e)
        return [], set(), set()

    adds, force, drop = [], set(), set()
    for o in ops:
        toon = (o.get('toon') or '').strip()
        if not toon:
            continue
        if o.get('act') == 'del':
            drop.add(toon.lower())
        else:
            force.add(toon.lower())
            adds.append({'id': (o.get('id') or '').strip() or toon.split('[')[0],
                         'ladderId': toon,
                         'tier': (o.get('tier') or '').strip()})
    if adds or drop:
        print('관리자 변경 %d건 — 추가 %d개, 빼기 %d개'
              % (len(ops), len(adds), len(drop)))
    return adds, force, drop


def merge(roster, reqs):
    """등록요청을 명단에 합쳐 파일로 저장한다.

    - 이미 그 래더계정이 명단에 적혀 있으면 아무것도 안 한다
    - 클랜ID 가 명단에 있으면 그 사람의 래더아이디 칸만 채운다
    - 없는 사람이면 새 줄로 넣는다
    """
    by_id = {r['id'].strip().lower(): r for r in roster}
    have = set(r['ladderId'].strip().lower() for r in roster if r.get('ladderId'))

    added, filled = [], []
    for q in reqs:
        low = q['ladderId'].lower()
        if low in have:
            continue
        have.add(low)
        old = by_id.get(q['id'].lower())
        if old:
            old['ladderId'] = q['ladderId']
            if not old.get('tier') and q['tier']:
                old['tier'] = q['tier']
            filled.append(q['ladderId'])
        else:
            r = {'id': q['id'], 'tier': q['tier'], 'race': '', 'note': '등록요청',
                 'tag': '', 'code': '', 'old': '', 'ladderId': q['ladderId']}
            roster.append(r)
            by_id[q['id'].lower()] = r
            added.append(q['ladderId'])

    if added or filled:
        with io.open(ROSTER, 'w', encoding='utf-8') as f:
            json.dump(roster, f, ensure_ascii=False, indent=1)
    return added, filled


def main():
    t0 = time.time()
    roster = json.load(open(ROSTER, encoding='utf-8'))

    # 홈페이지에서 들어온 등록요청 + 관리자 모드에서 넣은 것을 명단에 합친다
    adds, force, drop = admin()
    reqs = inbox() + adds
    if reqs:
        added, filled = merge(roster, reqs)
        print('등록요청 %d건 확인 — 새로 넣음 %d명, 래더아이디 채움 %d명'
              % (len(reqs), len(added), len(filled)))
        for t in added + filled:
            print('   + %s' % t)

    # 등록요청·관리자로 직접 들어온 계정은 [kS] 태그가 없어도 순위에 올린다.
    # (본인이 적어 보낸 계정이니 클랜원인 게 확인된 셈이다)
    force |= set(q['ladderId'].strip().lower() for q in reqs if q.get('ladderId'))
    force |= set(r['ladderId'].strip().lower() for r in roster
                 if r.get('note') == '등록요청' and r.get('ladderId'))

    ladder = sweep()
    if not ladder:
        print('래더를 하나도 못 받았습니다. 인터넷 연결을 확인하세요.')
        return

    ks_accounts = [a for a in ladder if KS.search(a['toon'])]
    print('전체 %d명 중 [kS] 태그 계정 %d개 발견' % (len(ladder), len(ks_accounts)))

    # 빠른 조회용 색인
    by_toon, by_base, by_tag = {}, {}, {}
    for a in ladder:
        by_toon.setdefault(a['toon'].lower(), []).append(a)
        tag = (a.get('battleTag') or '').lower()
        if tag:
            by_tag.setdefault(tag, []).append(a)
    for a in ks_accounts:
        by_base.setdefault(a['toon'].split('[')[0].strip().lower(), []).append(a)

    print('클랜원 %d명과 짝 맞추는 중' % len(roster))
    rows, missing, errors, used, need = [], [], [], set(), []

    def take(r, cands, how, sure):
        cands = sorted(cands, key=lambda a: -(a.get('rating') or 0))
        best = cands[0]
        row = row_of(r, best, how, sure)
        row['alts'] = [{'toon': a['toon'], 'gateway': a['gateway'],
                        'rating': a.get('rating') or 0} for a in cands[1:]]
        for a in cands:
            used.add((a['gateway'], a['toon']))
        return row

    # 받아둔 래더 2만명 안에서 짝을 찾는다 (통신이 없어 순식간이다)
    for r in roster:
        key = r['id'].strip().lower()
        row = None

        # 1) 래더아이디 칸
        if r.get('ladderId'):
            hit = by_toon.get(r['ladderId'].strip().lower())
            if hit:
                row = take(r, hit, '래더아이디', True)

        # 2) 클랜ID[kS]
        if row is None and key in by_base:
            row = take(r, by_base[key], 'kS태그', True)

        # 3) 배틀코드 이름이 래더에서 유일할 때만
        if row is None and r.get('code'):
            same = by_tag.get(r['code'].split('#')[0].strip().lower(), [])
            if len(same) == 1:
                row = take(r, same, '배틀코드', True)

        # 4) 클랜ID 와 똑같은 계정 (동명이인 가능 -> 추정)
        if row is None and key in by_toon:
            row = take(r, by_toon[key], '클랜ID', False)

        if row:
            rows.append(row)
        else:
            need.append(r)

    # 5) 래더 2만위 밖일 수 있으니 한 명씩 더 찾아본다 (동시에 물어봄)
    #    어차피 [kS] 붙은 계정만 순위에 올리므로 그 이름으로만 찾는다
    print('래더 2만위 밖 확인 중 (%d명)' % len(need))
    asked = [(r.get('ladderId') or (r['id'] + '[kS]')) for r in need]
    for r, res in zip(need, each(lookup, asked, '명')):
        acc, err = res if res else (None, True)
        if acc:
            rows.append(row_of(r, acc, '개별조회', True))
        elif err:
            errors.append(r['id'])
        else:
            missing.append(r['id'])

    # 6) 순위에 올릴 계정만 추린다 — [kS] 태그 + 클랜티어가 둘 다 있어야 한다.
    #    단 등록요청·관리자 모드로 직접 들어온 계정은 태그가 없어도 통과시키고,
    #    빼기로 표시한 계정은 조건에 맞아도 뺀다.
    found = len(rows)
    rows = [r for r in rows
            if (KS.search(r['toon']) or r['toon'].lower() in force)
            and r['tier'] in TIERS
            and r['toon'].lower() not in drop]
    cut = found - len(rows)

    # 7) 계정마다 종족별 전적과 최근 10전을 받아온다 (펼쳐보는 내용)
    print('상세 전적 받는 중 (%d개)' % len(rows))
    for row, d in zip(rows, each(lambda x: detail(x['gateway'], x['toon']),
                                 rows, '개', 25)):
        row['mu'], row['recent'], mmr = d if d else ({}, [], 0)
        if not row['rating'] and mmr:
            row['rating'], row['estMmr'] = mmr, True

    # cwal 이 한꺼번에 많이 물어보면 종종 빈손으로 답한다.
    # 그러면 계정을 눌러도 종족별 전적이 안 보이므로, 빈 것만 골라 다시 묻는다.
    for turn in (1, 2):
        again = [r for r in rows if not r['mu'] or not r['recent']]
        if not again:
            break
        print('  전적이 비어 온 %d개 다시 물어보는 중 (%d번째)' % (len(again), turn))
        time.sleep(3)
        for row, d in zip(again, each(lambda x: detail(x['gateway'], x['toon']),
                                      again, '개', 25)):
            if not d:
                continue
            mu, recent, mmr = d
            if mu:
                row['mu'] = mu
            if recent:
                row['recent'] = recent
            if not row['rating'] and mmr:
                row['rating'], row['estMmr'] = mmr, True
    left = len([r for r in rows if not r['mu']])
    if left:
        print('  (%d개는 끝내 전적을 못 받았습니다)' % left)

    est = len([r for r in rows if r.get('estMmr')])
    if est:
        print('  점수가 0 으로 온 %d개는 마지막 경기 기록에서 되살렸습니다' % est)

    # 7-2) 최근 경기의 리플레이 내려받기 주소
    replays(rows)

    rows.sort(key=lambda x: -x['rating'])
    data = {
        # 깃허브 서버는 세계표준시로 돌기 때문에 한국시간으로 고정해서 적는다
        'updated': datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=9))
        ).strftime('%Y-%m-%d %H:%M'),
        'total': len(roster), 'ladderSize': len(ladder),
        'rows': rows, 'missing': missing, 'errors': errors,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('window.KS_DATA = ')
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write(';\n')

    # 8) 순위표 페이지를 같은 틀에서 세 벌 찍어낸다
    #    index.html   : 옆의 data.js 를 읽어옴 (내 컴퓨터에서 보는 용도)
    #    공유용.html  : 데이터를 안에 통째로 넣음 (파일 하나만 보내면 되는 용도)
    #    공개/index.html : 인터넷에 올라가는 판. 내부용 항목은 빼고 넣는다
    try:
        tpl = open(TPL, encoding='utf-8').read()
        # 깃허브에서 자동으로 돌 때(KS_PUB)는 공개용 한 장만 만든다
        if PUB != LOCAL:
            with open(SHARE, 'w', encoding='utf-8') as f:
                f.write(page(tpl, '<script>window.KS_DATA = '
                        + json.dumps(data, ensure_ascii=False) + ';</script>'))
            with open(LOCAL, 'w', encoding='utf-8') as f:
                f.write(page(tpl, '<script src="data.js"></script>'))

        pub = {k: v for k, v in data.items() if k not in DROP_TOP}
        pub['rows'] = [{k: v for k, v in r.items() if k not in DROP_ROW}
                       for r in data['rows']]
        folder = os.path.dirname(PUB)      # 파일 이름만 있으면 만들 폴더도 없다
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(PUB, 'w', encoding='utf-8') as f:
            f.write(page(tpl, '<script>window.KS_DATA = '
                    + json.dumps(pub, ensure_ascii=False, separators=(',', ':'))
                    + ';</script>'))

        # 관리자 화면에서 '빼기' 를 누를 수 있게 지금 순위에 오른 계정만 따로 적어둔다
        with open(ACC, 'w', encoding='utf-8') as f:
            json.dump({'updated': data['updated'],
                       'rows': [{'id': r['id'], 'toon': r['toon'], 'tier': r['tier']}
                                for r in rows]},
                      f, ensure_ascii=False, separators=(',', ':'))

        # 등록요청이 들어왔는데 계정을 못 찾은 것 — 카페 안내글 봇이 읽어간다.
        # 봇은 이 파일의 글을 그대로 옮겨 붙이지 않고, 값만 꺼내 쓴다.
        못찾음 = 못찾은요청(reqs, rows)
        표식 = '|'.join(sorted('%s>%s' % (x['clanId'], x['ladderId'])
                               for x in 못찾음))
        with open(NF, 'w', encoding='utf-8') as f:
            json.dump({'updated': data['updated'],
                       'count': len(못찾음),
                       # 어제와 같은 상태면 글을 또 올리지 않도록 비교용 값
                       'sig': hashlib.sha1(표식.encode('utf-8')).hexdigest()[:12],
                       'rows': 못찾음},
                      f, ensure_ascii=False, separators=(',', ':'))
        if 못찾음:
            print('등록요청했는데 계정 못 찾음 : %d건' % len(못찾음))
            for x in 못찾음:
                print('   ? 클랜아이디 %s / 래더계정 %s'
                      % (x['clanId'] or '(빈칸)', x['ladderId'] or '(빈칸)'))
    except FileNotFoundError:
        # 틀 파일이 없을 때만 넘어간다. 조용히 지나가면 안 되니 알려준다.
        print('!! %s 를 못 찾아 순위표 페이지를 못 만들었습니다' % TPL)
        raise SystemExit(1)

    print('=' * 56)
    print('순위에 오른 계정   : %d개' % len(rows))
    print('  ([kS] 태그와 클랜티어가 둘 다 있는 계정 + 등록요청·관리자로 넣은 계정)')
    print('기준에 안 맞아 뺀 계정 : %d개' % cut)
    print('계정 못 찾은 클랜원 : %d명' % len(missing))
    print('통신오류           : %d명' % len(errors))
    print('걸린 시간          : %d초' % (time.time() - t0))
    print('=> %s 저장 완료.' % PUB)
    if PUB != LOCAL:
        print('=> %s / %s 도 저장했습니다. index.html 을 여세요.' % (OUT, LOCAL))
        print('=> %s 도 갱신했습니다 (파일 하나로 다 들어있는 판).' % SHARE)
    if errors:
        print('\n확인 실패(잠시 뒤 다시 실행하세요):', ', '.join(errors))


if __name__ == '__main__':
    main()
    if sys.platform == 'win32':
        try:
            input('\n엔터를 누르면 창이 닫힙니다...')
        except EOFError:
            pass
