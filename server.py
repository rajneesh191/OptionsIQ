#!/usr/bin/env python3
"""
server.py - OptionsIQ Local Data Server
========================================
Run this once and OptionsIQ.html gets live data automatically.
No API keys. No proxies. No registration. Completely free.

Usage:
  python server.py

Then open: http://localhost:8765/OptionsIQ.html
(Do NOT open the .html file directly - open via this server)

Requirements: pip install yfinance
"""

import json, math, datetime, threading, time, os, sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

# ── CONFIG ──────────────────────────────────────────────────────
PORT = 8765
CACHE_SECONDS = 300  # cache each stock for 5 minutes

# ── CACHE ───────────────────────────────────────────────────────
cache = {}

def calc_ivr(closes):
    if len(closes) < 10:
        return 35
    rets = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))
            if closes[i] > 0 and closes[i-1] > 0]
    if not rets:
        return 35
    mu = sum(rets) / len(rets)
    hv = math.sqrt(sum((r-mu)**2 for r in rets) / len(rets) * 252) * 100
    recent = rets[-10:]
    rm = sum(recent) / len(recent)
    rhv = math.sqrt(sum((r-rm)**2 for r in recent) / len(recent) * 252) * 100
    return min(95, max(5, round((rhv / (hv or 30)) * 50)))

def fetch_stock(symbol):
    # Check cache first
    now = time.time()
    if symbol in cache and now - cache[symbol]['ts'] < CACHE_SECONDS:
        print(f"  [CACHE] {symbol}")
        return cache[symbol]['data']

    try:
        import yfinance as yf
        print(f"  [FETCH] {symbol} from Yahoo Finance...")
        tk = yf.Ticker(symbol)
        hist = tk.history(period="3mo")
        info = tk.info

        closes = hist["Close"].tolist() if not hist.empty else []
        price = info.get("regularMarketPrice") or info.get("currentPrice") or (closes[-1] if closes else 0)
        if not price:
            return {"error": f"No price data for {symbol}"}

        prev  = info.get("regularMarketPreviousClose") or info.get("previousClose") or price
        chg   = price - prev
        chgPct = (chg / prev * 100) if prev else 0
        hi52  = info.get("fiftyTwoWeekHigh") or (max(closes) if closes else price*1.2)
        lo52  = info.get("fiftyTwoWeekLow")  or (min(closes) if closes else price*0.8)
        curr  = info.get("currency","USD")
        ccy   = "₹" if curr == "INR" else "$"
        exch  = info.get("exchangeName") or info.get("fullExchangeName") or ""
        ivr   = calc_ivr(closes)
        name  = info.get("longName") or info.get("shortName") or symbol

        result = {
            "symbol": symbol,
            "name": name,
            "price": round(price, 2),
            "change": round(chg, 2),
            "changePct": round(chgPct, 2),
            "hi52": round(hi52, 2),
            "lo52": round(lo52, 2),
            "ivrEst": ivr,
            "currency": curr,
            "ccy": ccy,
            "exchange": exch,
            "updatedAt": datetime.datetime.now().isoformat(),
            "source": "yfinance (local server)"
        }
        cache[symbol] = {"ts": now, "data": result}
        print(f"  [OK] {symbol}: {ccy}{price:.2f} | {chgPct:+.2f}% | IVR~{ivr}")
        return result

    except ImportError:
        return {"error": "yfinance not installed. Run: pip install yfinance"}
    except Exception as e:
        return {"error": str(e)}

# ── OPTION CHAIN FETCHER ────────────────────────────────────────
def fetch_option_chain(symbol):
    """Fetch option chain - NSE for Indian, yfinance for US."""
    import requests, datetime, time, math

    clean = symbol.replace(".NS","").replace(".BO","").upper()

    # US stocks -> yfinance
    us = ["AAPL","TSLA","NVDA","SPY","QQQ","MSFT","AMZN","META","GOOGL","AMD","JPM","LLY"]
    if clean in us:
        return fetch_option_chain_us(clean)

    print(f"  [OC] Fetching option chain for {clean} from NSE...")

    # Try multiple NSE-compatible sources
    sources = [
        # Source 1: unofficialapi (no auth needed)
        {
            "url": f"https://api.upstox.com/v2/option/chain?instrument_key=NSE_EQ%7CINE062A01020&expiry_date=2024-01-25",
            "type": "upstox"
        },
    ]

    # Best approach: use requests-html or a proper session
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })

    # Warm up NSE session properly
    try:
        print(f"  [OC] Warming up NSE session...")
        s1 = session.get("https://www.nseindia.com",
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                     "Referer": "https://www.google.com/"},
            timeout=12)
        print(f"  [OC] Step 1: {s1.status_code}, cookies: {len(session.cookies)}")
        time.sleep(2)

        s2 = session.get("https://www.nseindia.com/market-data/live-equity-market",
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                     "Referer": "https://www.nseindia.com/"},
            timeout=12)
        print(f"  [OC] Step 2: {s2.status_code}")
        time.sleep(1)

        s3 = session.get("https://www.nseindia.com/option-chain",
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                     "Referer": "https://www.nseindia.com/market-data/live-equity-market"},
            timeout=12)
        print(f"  [OC] Step 3: {s3.status_code}")
        time.sleep(2)
    except Exception as e:
        print(f"  [OC] Warmup error: {e}")

    # Now call the API
    indices = ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"]
    if clean in indices:
        api_url = f"https://www.nseindia.com/api/option-chain-indices?symbol={clean}"
    else:
        api_url = f"https://www.nseindia.com/api/option-chain-equities?symbol={clean}"

    try:
        r = session.get(api_url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.nseindia.com/option-chain",
                "X-Requested-With": "XMLHttpRequest",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            },
            timeout=20)
        print(f"  [OC] API: {r.status_code}, size: {len(r.content)}")

        if r.status_code != 200:
            return {"error": f"NSE returned {r.status_code}. Try again."}

        if len(r.content) < 500:
            # NSE blocked - try alternative
            print(f"  [OC] NSE blocked, trying alternative source...")
            return fetch_oc_alternative(clean)

        data = r.json()
    except Exception as e:
        print(f"  [OC] Error: {e}")
        return fetch_oc_alternative(clean)

    return parse_nse_oc(data, clean)


def fetch_oc_alternative(clean):
    """Alternative option chain source when NSE blocks."""
    import requests, datetime
    # Use opstra or sensibull alternative endpoints
    try:
        # Try yfinance for whatever we can get
        import yfinance as yf
        sym = clean + ".NS"
        tk = yf.Ticker(sym)
        spot_info = tk.info
        spot = spot_info.get("regularMarketPrice") or spot_info.get("currentPrice") or 0

        if not spot:
            return {"error": f"NSE is currently blocking automated requests for {clean}. This is NSE's bot protection. Please try: 1) Wait 2 minutes and retry, 2) Use NIFTY or BANKNIFTY instead, 3) Try US stocks like AAPL which always work."}

        # Generate synthetic option chain from spot price
        # This is estimated - not real OI data
        import math
        strikes = [round(spot * (1 + i*0.005) / 10) * 10 for i in range(-10, 11)]
        strikes = sorted(set(strikes))
        atm = min(strikes, key=lambda x: abs(x-spot))

        chain_rows = []
        for strike in strikes:
            dist = abs(strike - spot) / spot
            # Synthetic OI - higher near ATM
            base_oi = max(0, int(500000 * math.exp(-dist * 20)))
            chain_rows.append({
                "strike": float(strike),
                "ce_oi": base_oi if strike >= atm else base_oi//3,
                "ce_ltp": round(max(0.05, spot - strike + spot*0.02*math.exp(-dist*5)), 2) if strike <= spot else round(max(0.05, spot*0.015*math.exp(-dist*8)), 2),
                "ce_iv": round(18 + dist*100, 1),
                "ce_chg_oi": 0, "ce_vol": base_oi//10,
                "pe_oi": base_oi if strike <= atm else base_oi//3,
                "pe_ltp": round(max(0.05, strike - spot + spot*0.02*math.exp(-dist*5)), 2) if strike >= spot else round(max(0.05, spot*0.015*math.exp(-dist*8)), 2),
                "pe_iv": round(18 + dist*100, 1),
                "pe_chg_oi": 0, "pe_vol": base_oi//10,
            })

        atm_idx = strikes.index(atm)
        atm_row = next((r for r in chain_rows if r["strike"]==atm), {})

        return {
            "symbol": clean, "spot": round(spot,2),
            "expiry": "Estimated (NSE blocked)", "nextExpiry": None,
            "atm": atm, "pcr": 1.0, "maxPain": atm,
            "support": strikes[max(0,atm_idx-3)],
            "resistance": strikes[min(len(strikes)-1,atm_idx+3)],
            "recStrikes": {
                "atm": atm,
                "otm_call": strikes[min(atm_idx+1,len(strikes)-1)],
                "otm_put":  strikes[max(atm_idx-1,0)],
            },
            "atmRow": atm_row, "chain": chain_rows,
            "totalCallOI": 0, "totalPutOI": 0,
            "updatedAt": datetime.datetime.now().isoformat(),
            "source": "Estimated (NSE blocked - real OI unavailable)",
            "warning": "NSE blocked live OI data. Strikes are estimated from spot price. Real OI data unavailable."
        }
    except Exception as e:
        return {"error": f"NSE is blocking requests. Try again in 2 minutes or use AAPL/SPY for US option chain. Error: {str(e)}"}


def parse_nse_oc(data, clean):
    """Parse NSE API response into standard format."""
    import datetime
    try:
        filtered = data.get("filtered") or {}
        records  = data.get("records")  or {}
        spot = float(filtered.get("underlyingValue") or records.get("underlyingValue") or 0)
        expiry_dates = records.get("expiryDates") or filtered.get("expiryDates") or []
        all_data = filtered.get("data") or records.get("data") or []

        if not spot and all_data:
            for row in all_data[:5]:
                spot = float(row.get("CE",{}).get("underlyingValue",0) or row.get("PE",{}).get("underlyingValue",0) or 0)
                if spot: break

        if not spot or not all_data:
            return fetch_oc_alternative(clean)

        near_expiry = expiry_dates[0] if expiry_dates else "Unknown"
        next_expiry = expiry_dates[1] if len(expiry_dates)>1 else None

        chain_rows=[]; total_c=total_p=0
        for row in all_data:
            if expiry_dates and row.get("expiryDate") and row["expiryDate"]!=near_expiry:
                continue
            strike=float(row.get("strikePrice",0))
            ce=row.get("CE",{}) or {}; pe=row.get("PE",{}) or {}
            ce_oi=int(ce.get("openInterest",0) or 0)
            pe_oi=int(pe.get("openInterest",0) or 0)
            total_c+=ce_oi; total_p+=pe_oi
            chain_rows.append({
                "strike":strike,
                "ce_oi":ce_oi,"ce_ltp":round(float(ce.get("lastPrice",0) or 0),2),
                "ce_iv":round(float(ce.get("impliedVolatility",0) or 0),1),
                "ce_chg_oi":int(ce.get("changeinOpenInterest",0) or 0),
                "ce_vol":int(ce.get("totalTradedVolume",0) or 0),
                "pe_oi":pe_oi,"pe_ltp":round(float(pe.get("lastPrice",0) or 0),2),
                "pe_iv":round(float(pe.get("impliedVolatility",0) or 0),1),
                "pe_chg_oi":int(pe.get("changeinOpenInterest",0) or 0),
                "pe_vol":int(pe.get("totalTradedVolume",0) or 0),
            })

        chain_rows.sort(key=lambda x:x["strike"])
        strikes=[r["strike"] for r in chain_rows]
        atm=min(strikes,key=lambda x:abs(x-spot)) if strikes else spot
        pcr=round(total_p/total_c,2) if total_c else 0
        support=max(chain_rows,key=lambda x:x["pe_oi"])["strike"] if chain_rows else 0
        resistance=max(chain_rows,key=lambda x:x["ce_oi"])["strike"] if chain_rows else 0

        def mp(rows,stks):
            best=stks[0];bv=float("inf")
            for s in stks:
                v=sum(max(0,s-r["strike"])*r["ce_oi"]+max(0,r["strike"]-s)*r["pe_oi"] for r in rows)
                if v<bv:bv=v;best=s
            return best

        atm_idx=strikes.index(atm) if atm in strikes else len(strikes)//2
        atm_row=next((r for r in chain_rows if r["strike"]==atm),{})
        print(f"  [OC] Parsed: spot={spot}, ATM={atm}, PCR={pcr}, rows={len(chain_rows)}")
        return {
            "symbol":clean,"spot":round(spot,2),
            "expiry":near_expiry,"nextExpiry":next_expiry,
            "atm":atm,"pcr":pcr,"maxPain":mp(chain_rows,strikes),
            "support":support,"resistance":resistance,
            "recStrikes":{"atm":strikes[atm_idx],
                "otm_call":strikes[min(atm_idx+1,len(strikes)-1)],
                "otm_put":strikes[max(atm_idx-1,0)]},
            "atmRow":atm_row,"chain":chain_rows,
            "totalCallOI":total_c,"totalPutOI":total_p,
            "updatedAt":datetime.datetime.now().isoformat(),
            "source":"NSE India"
        }
    except Exception as e:
        import traceback
        return {"error":f"Parse error: {str(e)}","trace":traceback.format_exc()[:300]}


def fetch_option_chain_us(symbol):
    """Fetch option chain for US stocks via yfinance."""
    import yfinance as yf, datetime, math
    try:
        tk = yf.Ticker(symbol)
        info = tk.info
        spot = info.get("regularMarketPrice") or info.get("currentPrice") or 0
        expiries = list(tk.options)
        if not expiries:
            return {"error": f"No options data for {symbol}"}

        chain = tk.option_chain(expiries[0])
        calls, puts = chain.calls, chain.puts

        call_strikes = set(calls["strike"].tolist())
        put_strikes  = set(puts["strike"].tolist())
        all_strikes  = sorted(call_strikes | put_strikes)
        atm = min(all_strikes, key=lambda x: abs(x-spot))

        rows = []
        total_c = total_p = 0
        for strike in all_strikes:
            cr = calls[calls["strike"]==strike]
            pr = puts[puts["strike"]==strike]
            def safe(df, col):
                try: v=df[col].iloc[0]; return 0 if (v!=v) else v
                except: return 0
            ce_oi=int(safe(cr,"openInterest")); pe_oi=int(safe(pr,"openInterest"))
            total_c+=ce_oi; total_p+=pe_oi
            rows.append({"strike":float(strike),
                "ce_oi":ce_oi,"ce_ltp":round(float(safe(cr,"lastPrice")),2),
                "ce_iv":round(float(safe(cr,"impliedVolatility"))*100,1),
                "ce_chg_oi":0,"ce_vol":int(safe(cr,"volume")),
                "pe_oi":pe_oi,"pe_ltp":round(float(safe(pr,"lastPrice")),2),
                "pe_iv":round(float(safe(pr,"impliedVolatility"))*100,1),
                "pe_chg_oi":0,"pe_vol":int(safe(pr,"volume"))})

        pcr = round(total_p/total_c,2) if total_c else 0
        support    = max(rows,key=lambda x:x["pe_oi"])["strike"] if rows else 0
        resistance = max(rows,key=lambda x:x["ce_oi"])["strike"] if rows else 0
        atm_idx = all_strikes.index(atm) if atm in all_strikes else len(all_strikes)//2
        atm_row = next((r for r in rows if r["strike"]==atm), {})

        def mp(rows,stks):
            best=stks[0]; bv=float("inf")
            for s in stks:
                v=sum(max(0,s-r["strike"])*r["ce_oi"]+max(0,r["strike"]-s)*r["pe_oi"] for r in rows)
                if v<bv: bv=v; best=s
            return best

        return {"symbol":symbol,"spot":round(spot,2),
            "expiry":expiries[0],"nextExpiry":expiries[1] if len(expiries)>1 else None,
            "atm":atm,"pcr":pcr,"maxPain":mp(rows,all_strikes),
            "support":support,"resistance":resistance,
            "recStrikes":{"atm":all_strikes[atm_idx],
                "otm_call":all_strikes[min(atm_idx+1,len(all_strikes)-1)],
                "otm_put":all_strikes[max(atm_idx-1,0)]},
            "atmRow":atm_row,"chain":rows,
            "totalCallOI":total_c,"totalPutOI":total_p,
            "updatedAt":datetime.datetime.now().isoformat(),"source":"yfinance"}
    except Exception as e:
        return {"error":str(e)}


# ── HTTP HANDLER ─────────────────────────────────────────────────
class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        # CORS headers for all responses
        if self.path.startswith("/optionchain?"):
            symbol = self.path.split("symbol=")[-1].split("&")[0].strip()
            data = fetch_option_chain(symbol)
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/test"):
            # Test which symbols have options data
            import yfinance as yf
            test_syms = ["SBIN.NS","RELIANCE.NS","TCS.NS","INFY.NS","^NSEI","^NSEBANK","AAPL","SPY","NIFTY.NS"]
            results = {}
            for sym in test_syms:
                try:
                    tk = yf.Ticker(sym)
                    opts = list(tk.options) if tk.options else []
                    results[sym] = {"expiries": len(opts), "first": opts[0] if opts else None}
                except Exception as e:
                    results[sym] = {"error": str(e)[:80]}
            body = json.dumps(results, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/quote?"):
            symbol = self.path.split("symbol=")[-1].split("&")[0].strip()
            data = fetch_stock(symbol)
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/batch?"):
            symbols = self.path.split("symbols=")[-1].split("&")[0].strip().split(",")
            results = {}
            for sym in symbols:
                sym = sym.strip()
                if sym:
                    results[sym.replace(".NS","").replace(".BO","")] = fetch_stock(sym)
            body = json.dumps(results).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.endswith('.ico') or self.path.endswith('.png'):
            # Suppress favicon errors silently
            self.send_response(204)
            self.end_headers()
        else:
            # Serve static files (HTML, JS, JSON etc.)
            try:
                super().do_GET()
            except Exception:
                pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def log_message(self, format, *args):
        try:
            msg = str(args[0]) if args else ''
            if '/quote?' in msg or '/batch?' in msg:
                pass  # already logged in fetch_stock
            elif '.ico' in msg or '.png' in msg or '.css' in msg:
                pass  # suppress favicon/asset errors
            elif args[1:] and str(args[1]) not in ['200']:
                pass  # suppress non-200 noise
        except Exception:
            pass

# ── MAIN ─────────────────────────────────────────────────────────
def main():
    # Railway sets PORT env variable - use it, fallback to 8765 locally
    port = int(os.environ.get("PORT", PORT))
    
    # Check yfinance
    try:
        import yfinance
        print(f"  yfinance: OK")
    except ImportError:
        print("  ERROR: yfinance not installed!")
        sys.exit(1)

    # Change to script directory so HTML files are served
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Listen on 0.0.0.0 for cloud, localhost for local
    host = "0.0.0.0"
    server = HTTPServer((host, port), Handler)
    
    print("=" * 55)
    print("  OptionsIQ Server")
    print("=" * 55)
    if port == 8765:
        print(f"  Local URL: http://localhost:{port}/OptionsIQ.html")
    else:
        print(f"  Running on port {port} (cloud mode)")
    print("=" * 55)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")

if __name__ == "__main__":
    main()

