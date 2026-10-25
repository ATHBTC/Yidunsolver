"""YiDun (网易易盾) 滑块拼图自动验证 —— 通过 Chrome DevTools Protocol (CDP) 驱动。
YiDun (NetEase) slide-puzzle solver driven over CDP.

Approach
--------
1. scrollIntoView the .yidun widget (the puzzle panel only renders when visible)
2. read .yidun_bg-img / .yidun_jigsaw src, download both directly (public CDN)
3. locate the gap: the notch is rendered as a lightened / desaturated copy of the
   piece shape, so correlate the piece's alpha mask against (inside-brightness
   minus surrounding-ring brightness) and the same for saturation; the combined
   score has a single sharp peak.
4. convert natural-image px to on-screen px via displayed/natural width ratio
5. drag .yidun_slider with Input.dispatchMouseEvent on a human-ish trajectory
   (ease-out, y jitter, 0.85-1.4 s, overshoot + small pull-back)

用法
----
  # 1) 启动带调试端口的 Chrome/Chromium，打开出现滑块的页面
  chromium --remote-debugging-port=9222
  # 2) 运行（参数是目标标签页 URL 里的一段关键字）
  python3 yidun_solver.py passport.example.com
  # 也可指定 CDP 地址: --cdp http://127.0.0.1:9222  尝试次数: --tries 5

  # 在自己的代码里:
  from yidun_solver import Tab, solve
  r = solve(Tab('login'))      # {'status': 'ok', ...}

依赖: pip install numpy opencv-python-headless websocket-client
"""
import json, time, random, math, sys, argparse, urllib.request
import numpy as np, cv2
import websocket

_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CDP = 'http://127.0.0.1:9222'


class Tab:
    """最小 CDP 客户端：按 URL 片段连接一个已打开的标签页。"""
    def __init__(s, match, cdp=None):
        base = (cdp or CDP).rstrip('/')
        pages = [p for p in json.load(_op.open(base + '/json/list')) if p['type'] == 'page' and match in p['url']]
        if not pages:
            raise SystemExit('没有 URL 包含 %r 的标签页' % match)
        s.ws = websocket.create_connection(pages[0]['webSocketDebuggerUrl'], suppress_origin=True)
        s.n = 0
    def call(s, m, p=None):
        s.n += 1; i = s.n
        s.ws.send(json.dumps({'id': i, 'method': m, 'params': p or {}}))
        while True:
            r = json.loads(s.ws.recv())
            if r.get('id') == i: return r
    def ev(s, e):
        return s.call('Runtime.evaluate', {'expression': e, 'returnByValue': True, 'awaitPromise': True})['result']['result'].get('value')
    def mouse(s, typ, x, y, btn='left', buttons=None):
        p = {'type': typ, 'x': x, 'y': y, 'button': btn, 'clickCount': 1 if typ != 'mouseMoved' else 0}
        if buttons is not None: p['buttons'] = buttons
        s.call('Input.dispatchMouseEvent', p)


_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36'

def _fetch(url, referer):
    r = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': referer})
    return _op.open(r, timeout=20).read()

def _dec(buf, flags=cv2.IMREAD_UNCHANGED):
    return cv2.imdecode(np.frombuffer(buf, np.uint8), flags)

def gap_x(bg_bytes, piece_bytes):
    """x (natural px) where the piece image's left edge must end up, + confidence.
    Matches the piece's alpha-outline against Canny edges of the background in the
    piece's own row band (the notch always has a crisp outline, whether it is
    rendered lighter or darker). Verified against visual ground truth 7/7."""
    bg = _dec(bg_bytes, cv2.IMREAD_COLOR)
    pc = _dec(piece_bytes, cv2.IMREAD_UNCHANGED)
    if bg is None or pc is None or pc.ndim < 3 or pc.shape[2] != 4:
        return None, 0.0
    a = pc[:, :, 3]
    ys, xs = np.where(a > 40)
    if not len(xs): return None, 0.0
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    m = (a[y0:y1 + 1, x0:x1 + 1] > 40).astype(np.uint8) * 255
    w = m.shape[1]
    band = cv2.cvtColor(bg[y0:y1 + 1], cv2.COLOR_BGR2GRAY)
    e = cv2.Canny(band, 40, 140).astype(np.float32)
    outline = cv2.Canny(m, 50, 150).astype(np.float32)
    r = cv2.matchTemplate(e, outline, cv2.TM_CCORR_NORMED)[0]
    r[:w] = -9                       # gap never overlaps the piece's start strip
    i = int(np.argmax(r))
    r2 = r.copy(); r2[max(0, i - 12):i + 12] = -9
    conf = float(r[i] - r2.max())    # margin over the runner-up
    return float(i - x0), conf

def _traj(dist):
    over = max(2.0, dist * 0.035) if dist > 40 else 0.0
    total = random.uniform(0.85, 1.4)
    n = max(30, int(total / 0.016))
    peak = dist + over
    pts = []
    for i in range(1, n + 1):
        t = i / n
        e = 1 - (1 - t) ** 3                       # accelerate then decelerate
        pts.append((peak * e + random.uniform(-0.5, 0.5),
                    math.sin(t * math.pi * random.uniform(1.0, 2.0)) * random.uniform(0.5, 2.0),
                    total / n))
    for j in range(1, 7):                          # pull back off the overshoot
        pts.append((peak - over * j / 6 + random.uniform(-0.3, 0.3),
                    random.uniform(-1, 1), random.uniform(0.02, 0.05)))
    return pts

def present(pg):
    return bool(pg.ev("!!document.querySelector('.yidun_slider')"))

def _state(pg):
    return pg.ev("""(()=>{const w=document.querySelector('.yidun');const t=document.querySelector('.yidun_tips__text');
      return [w?w.className:'', t?t.innerText:'']})()""") or ['', '']

def solve_once(pg, verbose=True):
    if not present(pg): return {'status': 'no-slider'}
    pg.ev("document.querySelector('.yidun').scrollIntoView({block:'center'})")
    time.sleep(0.8)
    sl = pg.ev("(()=>{const e=document.querySelector('.yidun_slider');if(!e)return null;const r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()")
    if not sl: return {'status': 'no-slider'}
    sx, sy = sl
    pg.mouse('mouseMoved', sx - 25, sy - 6, 'none'); time.sleep(.25)
    pg.mouse('mouseMoved', sx, sy, 'none'); time.sleep(random.uniform(.5, .9))
    pg.mouse('mousePressed', sx, sy, buttons=1); time.sleep(.25)
    for d in (2, 5, 9):
        pg.mouse('mouseMoved', sx + d, sy + random.uniform(-1, 1), 'none', buttons=1); time.sleep(.07)
    time.sleep(.9)
    info = pg.ev("""(()=>{const b=document.querySelector('.yidun_bg-img'),p=document.querySelector('.yidun_jigsaw');
      if(!b||!p)return null;const rb=b.getBoundingClientRect(),rp=p.getBoundingClientRect();
      return [b.src,p.src,b.naturalWidth,rb.width,rp.x-rb.x,location.href]})()""")
    if not info or not info[0] or not info[3]:
        pg.mouse('mouseReleased', sx + 9, sy, buttons=0)
        return {'status': 'no-images', 'info': info}
    bsrc, psrc, nw, dw, cur_left, href = info
    try:
        tgt, conf = gap_x(_fetch(bsrc, href), _fetch(psrc, href))
    except Exception as e:
        pg.mouse('mouseReleased', sx + 9, sy, buttons=0)
        return {'status': 'fetch-fail', 'err': str(e)[:160]}
    if tgt is None:
        pg.mouse('mouseReleased', sx + 9, sy, buttons=0)
        return {'status': 'no-match'}
    scale = dw / nw
    target_screen = tgt * scale            # where the piece element's left must land
    if verbose: print('   gap=%.0f conf=%.3f scale=%.3f cur=%.1f target=%.1f' % (tgt, conf, scale, cur_left, target_screen))
    # slider travel maps ~1:1 onto piece travel after a short slow-start ramp,
    # so aim open-loop then close the loop on the piece's measured position.
    dx = target_screen - cur_left + 9
    for ddx, dy, dt in _traj(max(dx, 1)):
        pg.mouse('mouseMoved', sx + ddx, sy + dy, 'none', buttons=1)
        time.sleep(dt)
    cx = dx
    for _ in range(5):
        time.sleep(0.12)
        now = pg.ev("""(()=>{const b=document.querySelector('.yidun_bg-img'),p=document.querySelector('.yidun_jigsaw');
          if(!b||!p)return null;return p.getBoundingClientRect().x-b.getBoundingClientRect().x})()""")
        if now is None: break
        err = target_screen - now
        if abs(err) < 0.6: break
        cx += err
        steps = max(2, min(6, int(abs(err))))
        for k in range(1, steps + 1):
            pg.mouse('mouseMoved', sx + cx - err * (1 - k / steps), sy + random.uniform(-0.8, 0.8), 'none', buttons=1)
            time.sleep(random.uniform(.02, .05))
    time.sleep(random.uniform(.12, .25))
    pg.mouse('mouseReleased', sx + cx, sy, buttons=0)
    time.sleep(2.2)
    cls, tip = _state(pg)
    ok = ('yidun--success' in cls) or ('验证成功' in (tip or '')) or not present(pg)
    return {'status': 'ok' if ok else 'fail', 'tip': tip, 'cls': cls[-40:], 'conf': round(conf, 2), 'gap': tgt, 'moved': round(cx, 1)}

def solve(pg, tries=5, verbose=True):
    last = None
    for i in range(tries):
        last = solve_once(pg, verbose)
        if verbose: print('  try%d -> %s' % (i + 1, last))
        if last.get('status') in ('ok', 'no-slider'): return last
        pg.ev("(()=>{const e=document.querySelector('.yidun_refresh');if(e)e.click()})()")
        time.sleep(random.uniform(1.3, 2.2))
    return last


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Solve a YiDun slider in an open Chrome tab via CDP')
    ap.add_argument('match', help='目标标签页 URL 中的关键字')
    ap.add_argument('--cdp', default=CDP)
    ap.add_argument('--tries', type=int, default=5)
    a = ap.parse_args()
    r = solve(Tab(a.match, a.cdp), a.tries)
    print(json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r.get('status') in ('ok', 'no-slider') else 1)
