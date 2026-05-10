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
    """Fetch NSE option chain - tries multiple sources."""
    import urllib.parse, datetime

    clean = urllib.parse.unquote(symbol).replace(" ","").replace(".NS","").replace(".BO","").upper()
    name_map = {'NIFTY50':'NIFTY','BANKNIFTY50':'BANKNIFTY','BANK NIFTY':'BANKNIFTY'}
    clean = name_map.get(clean, clean)

    us = ["AAPL","TSLA","NVDA","SPY","QQQ","MSFT","AMZN","META","GOOGL","AMD","JPM","LLY","PLTR"]
    if clean in us:
        return fetch_option_chain_us(clean)

    indices = ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"]
    is_index = clean in indices

    print(f"  [OC] Fetching {clean} — trying all sources...")

    # Method 1: Sensibull (NSE official partner — less blocking)
    result = try_sensibull(clean, is_index)
    if result and 'error' not in result:
        return result

    # Method 2: Opstra (another reliable source)
    result = try_opstra(clean, is_index)
    if result and 'error' not in result:
        return result

    # Method 3: curl_cffi (Chrome impersonation)
    result = try_curl_cffi(clean, is_index)
    if result and 'error' not in result:
        return result

    # Method 4: Standard requests session
    result = try_requests_session(clean, is_index)
    if result and 'error' not in result:
        return result

    # Method 5: Estimated fallback
    return fetch_oc_alternative(clean)


def try_curl_cffi(clean, is_index):
    """Use curl_cffi to impersonate Chrome browser — bypasses NSE bot detection."""
    try:
        from curl_cffi import requests as curl_requests
        import time

        print(f"  [OC] Trying curl_cffi (Chrome impersonation)...")
        session = curl_requests.Session(impersonate="chrome120")

        # Warmup
        session.get("https://www.nseindia.com", timeout=15)
        time.sleep(2)
        session.get("https://www.nseindia.com/option-chain", timeout=15)
        time.sleep(2)

        if is_index:
            url = f"https://www.nseindia.com/api/option-chain-indices?symbol={clean}"
        else:
            url = f"https://www.nseindia.com/api/option-chain-equities?symbol={clean}"

        r = session.get(url, headers={"Referer":"https://www.nseindia.com/option-chain"}, timeout=20)
        print(f"  [OC] curl_cffi: {r.status_code}, size: {len(r.content)}")

        if r.status_code == 200 and len(r.content) > 500:
            data = r.json()
            result = parse_nse_oc(data, clean)
            if 'error' not in result:
                print(f"  [OC] curl_cffi SUCCESS!")
                return result
    except ImportError:
        print(f"  [OC] curl_cffi not available, trying requests...")
    except Exception as e:
        print(f"  [OC] curl_cffi error: {e}")
    return None


def try_requests_session(clean, is_index):
    """Standard requests session with browser headers."""
    import requests, time

    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    try:
        session.get("https://www.nseindia.com",
            headers={**headers,"Accept":"text/html,application/xhtml+xml,*/*;q=0.8",
                     "sec-fetch-site":"cross-site","sec-fetch-mode":"navigate","sec-fetch-dest":"document"},
            timeout=15)
        time.sleep(2)
        session.get("https://www.nseindia.com/option-chain",
            headers={**headers,"Accept":"text/html,application/xhtml+xml,*/*;q=0.8",
                     "Referer":"https://www.nseindia.com/",
                     "sec-fetch-site":"same-origin","sec-fetch-mode":"navigate","sec-fetch-dest":"document"},
            timeout=15)
        time.sleep(2)

        api_url = f"https://www.nseindia.com/api/option-chain-{'indices' if is_index else 'equities'}?symbol={clean}"
        r = session.get(api_url,
            headers={**headers,"Accept":"application/json, text/plain, */*",
                     "Referer":"https://www.nseindia.com/option-chain",
                     "sec-fetch-site":"same-origin","sec-fetch-mode":"cors","sec-fetch-dest":"empty",
                     "X-Requested-With":"XMLHttpRequest"},
            timeout=20)
        print(f"  [OC] requests: {r.status_code}, size: {len(r.content)}")

        if r.status_code == 200 and len(r.content) > 500:
            return parse_nse_oc(r.json(), clean)
    except Exception as e:
        print(f"  [OC] requests error: {e}")
    return None




def try_sensibull(clean, is_index):
    """Try Sensibull API - NSE official partner, less likely to block."""
    import requests, time
    try:
        print(f"  [OC] Trying Sensibull for {clean}...")
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://sensibull.com",
            "Referer": "https://sensibull.com/",
        }
        # Sensibull uses NSE symbol format
        sym = clean
        # Get expiry dates first
        exp_url = f"https://oxide.sensibull.com/v1/compute/cache/instrument_expiries/{sym}"
        r1 = session.get(exp_url, headers=headers, timeout=10)
        print(f"  [OC] Sensibull expiries: {r1.status_code}")
        if r1.status_code != 200:
            return None
        expiries = r1.json()
        if not expiries or not isinstance(expiries, list):
            return None
        near_expiry = expiries[0]
        
        # Get option chain
        oc_url = f"https://oxide.sensibull.com/v1/compute/cache/live_option_chain/{sym}/{near_expiry}"
        r2 = session.get(oc_url, headers=headers, timeout=15)
        print(f"  [OC] Sensibull chain: {r2.status_code}, size: {len(r2.content)}")
        if r2.status_code != 200 or len(r2.content) < 100:
            return None
        data = r2.json()
        return parse_sensibull_oc(data, clean, near_expiry, expiries)
    except Exception as e:
        print(f"  [OC] Sensibull error: {e}")
        return None


def try_opstra(clean, is_index):
    """Try Opstra free option chain API."""
    import requests
    try:
        print(f"  [OC] Trying Opstra for {clean}...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://opstra.definedge.com/",
        }
        url = f"https://opstra.definedge.com/api/openinterest/optionchain/{clean}"
        r = requests.get(url, headers=headers, timeout=12)
        print(f"  [OC] Opstra: {r.status_code}, size: {len(r.content)}")
        if r.status_code == 200 and len(r.content) > 200:
            data = r.json()
            return parse_opstra_oc(data, clean)
    except Exception as e:
        print(f"  [OC] Opstra error: {e}")
    return None


def parse_sensibull_oc(data, clean, near_expiry, all_expiries):
    """Parse Sensibull option chain response."""
    import datetime, math
    try:
        spot = float(data.get('underlyingValue') or data.get('spot') or data.get('ltp') or 0)
        rows_raw = data.get('optionChain') or data.get('data') or data.get('strikes') or []
        
        if not spot or not rows_raw:
            return None
            
        chain_rows = []
        total_c = total_p = 0
        
        for row in rows_raw:
            strike = float(row.get('strikePrice') or row.get('strike') or 0)
            if not strike: continue
            ce = row.get('CE') or row.get('callOption') or row.get('call') or {}
            pe = row.get('PE') or row.get('putOption') or row.get('put') or {}
            if isinstance(ce, (int, float)): ce = {}
            if isinstance(pe, (int, float)): pe = {}
            ce_oi = int(ce.get('openInterest') or ce.get('oi') or 0)
            pe_oi = int(pe.get('openInterest') or pe.get('oi') or 0)
            total_c += ce_oi; total_p += pe_oi
            chain_rows.append({
                'strike': strike,
                'ce_oi': ce_oi,
                'ce_ltp': round(float(ce.get('lastPrice') or ce.get('ltp') or 0), 2),
                'ce_iv': round(float(ce.get('impliedVolatility') or ce.get('iv') or 0), 1),
                'ce_chg_oi': int(ce.get('changeinOpenInterest') or ce.get('oiChange') or 0),
                'ce_vol': int(ce.get('totalTradedVolume') or ce.get('volume') or 0),
                'pe_oi': pe_oi,
                'pe_ltp': round(float(pe.get('lastPrice') or pe.get('ltp') or 0), 2),
                'pe_iv': round(float(pe.get('impliedVolatility') or pe.get('iv') or 0), 1),
                'pe_chg_oi': int(pe.get('changeinOpenInterest') or pe.get('oiChange') or 0),
                'pe_vol': int(pe.get('totalTradedVolume') or pe.get('volume') or 0),
            })
        
        if not chain_rows: return None
        chain_rows.sort(key=lambda x: x['strike'])
        strikes = [r['strike'] for r in chain_rows]
        atm = min(strikes, key=lambda x: abs(x - spot))
        pcr = round(total_p / total_c, 2) if total_c else 0
        support = max(chain_rows, key=lambda x: x['pe_oi'])['strike'] if chain_rows else 0
        resistance = max(chain_rows, key=lambda x: x['ce_oi'])['strike'] if chain_rows else 0
        
        def max_pain(rows, stks):
            best = stks[0]; bv = float('inf')
            for s in stks:
                v = sum(max(0,s-r['strike'])*r['ce_oi']+max(0,r['strike']-s)*r['pe_oi'] for r in rows)
                if v < bv: bv=v; best=s
            return best
        
        atm_idx = strikes.index(atm) if atm in strikes else len(strikes)//2
        atm_row = next((r for r in chain_rows if r['strike']==atm), {})
        
        print(f"  [OC] Sensibull parsed: spot={spot}, ATM={atm}, PCR={pcr}, rows={len(chain_rows)}")
        return {
            'symbol': clean, 'spot': round(spot,2),
            'expiry': near_expiry, 'nextExpiry': all_expiries[1] if len(all_expiries)>1 else None,
            'atm': atm, 'pcr': pcr, 'maxPain': max_pain(chain_rows, strikes),
            'support': support, 'resistance': resistance,
            'recStrikes': {'atm': strikes[atm_idx],
                'otm_call': strikes[min(atm_idx+1,len(strikes)-1)],
                'otm_put': strikes[max(atm_idx-1,0)]},
            'atmRow': atm_row, 'chain': chain_rows,
            'totalCallOI': total_c, 'totalPutOI': total_p,
            'updatedAt': datetime.datetime.now().isoformat(),
            'source': 'Sensibull'
        }
    except Exception as e:
        print(f"  [OC] Sensibull parse error: {e}")
        return None


def parse_opstra_oc(data, clean):
    """Parse Opstra option chain response."""
    import datetime
    try:
        # Opstra returns different format - try to extract
        records = data.get('data') or data.get('records') or data.get('OC') or []
        spot = float(data.get('underlyingValue') or data.get('spot') or 0)
        expiry = data.get('expiry') or 'Current'
        
        if not records: return None
        
        chain_rows = []
        total_c = total_p = 0
        for row in records:
            strike = float(row.get('strikePrice') or row.get('SP') or 0)
            if not strike: continue
            ce_oi = int(row.get('CE_OI') or row.get('callOI') or 0)
            pe_oi = int(row.get('PE_OI') or row.get('putOI') or 0)
            total_c += ce_oi; total_p += pe_oi
            if not spot and row.get('underlyingValue'):
                spot = float(row['underlyingValue'])
            chain_rows.append({
                'strike': strike,
                'ce_oi': ce_oi,
                'ce_ltp': round(float(row.get('CE_LTP') or row.get('callLTP') or 0),2),
                'ce_iv': round(float(row.get('CE_IV') or row.get('callIV') or 0),1),
                'ce_chg_oi': int(row.get('CE_CHNG_OI') or 0),
                'ce_vol': int(row.get('CE_Vol') or row.get('callVol') or 0),
                'pe_oi': pe_oi,
                'pe_ltp': round(float(row.get('PE_LTP') or row.get('putLTP') or 0),2),
                'pe_iv': round(float(row.get('PE_IV') or row.get('putIV') or 0),1),
                'pe_chg_oi': int(row.get('PE_CHNG_OI') or 0),
                'pe_vol': int(row.get('PE_Vol') or row.get('putVol') or 0),
            })
        
        if not chain_rows or not spot: return None
        chain_rows.sort(key=lambda x: x['strike'])
        strikes = [r['strike'] for r in chain_rows]
        atm = min(strikes, key=lambda x: abs(x-spot))
        pcr = round(total_p/total_c,2) if total_c else 0
        support = max(chain_rows,key=lambda x:x['pe_oi'])['strike'] if chain_rows else 0
        resistance = max(chain_rows,key=lambda x:x['ce_oi'])['strike'] if chain_rows else 0
        atm_idx = strikes.index(atm) if atm in strikes else len(strikes)//2
        atm_row = next((r for r in chain_rows if r['strike']==atm),{})
        
        def mp(rows,stks):
            best=stks[0];bv=float('inf')
            for s in stks:
                v=sum(max(0,s-r['strike'])*r['ce_oi']+max(0,r['strike']-s)*r['pe_oi'] for r in rows)
                if v<bv:bv=v;best=s
            return best
        
        print(f"  [OC] Opstra parsed: spot={spot}, ATM={atm}, rows={len(chain_rows)}")
        return {
            'symbol':clean,'spot':round(spot,2),
            'expiry':expiry,'nextExpiry':None,
            'atm':atm,'pcr':pcr,'maxPain':mp(chain_rows,strikes),
            'support':support,'resistance':resistance,
            'recStrikes':{'atm':strikes[atm_idx],
                'otm_call':strikes[min(atm_idx+1,len(strikes)-1)],
                'otm_put':strikes[max(atm_idx-1,0)]},
            'atmRow':atm_row,'chain':chain_rows,
            'totalCallOI':total_c,'totalPutOI':total_p,
            'updatedAt':datetime.datetime.now().isoformat(),
            'source':'Opstra'
        }
    except Exception as e:
        print(f"  [OC] Opstra parse error: {e}")
        return None


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
            return {"error": f"Could not get data for {clean}. NSE bot protection is active. Please: 1) Wait 1 minute and click Fetch again, 2) Restart server.py in CMD, 3) Try AAPL or SPY (US stocks always work via yfinance)"}

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
        elif self.path.startswith("/technical?"):
            symbol = self.path.split("symbol=")[-1].split("&")[0].strip()
            data = fetch_technical(symbol)
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Access-Control-Allow-Origin","*")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/nse-oc-cookie?"):
            # Fetch NSE option chain using cookies passed from browser
            import urllib.parse as up
            params = dict(up.parse_qsl(self.path.split("?",1)[1]))
            symbol = params.get("symbol","NIFTY").upper()
            cookies = params.get("cookies","")
            data = fetch_oc_with_browser_cookies(symbol, cookies)
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Access-Control-Allow-Origin","*")
            self.send_header("Content-Length",str(len(body)))
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

def fetch_technical(symbol):
    """Calculate RSI, MACD, EMA, Bollinger Bands, Volume trend from price history."""
    import yfinance as yf
    import math, datetime

    try:
        sym = symbol if '.' in symbol else symbol+'.NS'
        tk = yf.Ticker(sym)
        hist = tk.history(period="6mo")
        if hist.empty or len(hist)<30:
            return {"error":"Not enough data for "+symbol}

        closes = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()
        highs   = hist["High"].tolist()
        lows    = hist["Low"].tolist()
        dates   = [str(d.date()) for d in hist.index]

        def ema(data, period):
            e = [sum(data[:period])/period]
            k = 2/(period+1)
            for i in range(period, len(data)):
                e.append(data[i]*k + e[-1]*(1-k))
            return e

        # RSI (14)
        def calc_rsi(prices, period=14):
            gains, losses = [], []
            for i in range(1,len(prices)):
                d = prices[i]-prices[i-1]
                gains.append(max(d,0)); losses.append(max(-d,0))
            if len(gains)<period: return 50
            ag = sum(gains[:period])/period
            al = sum(losses[:period])/period
            rsi_vals = []
            for i in range(period, len(gains)):
                ag = (ag*13+gains[i])/14
                al = (al*13+losses[i])/14
                rs = ag/al if al else 100
                rsi_vals.append(100-100/(1+rs))
            return round(rsi_vals[-1],1) if rsi_vals else 50

        # MACD (12,26,9)
        def calc_macd(prices):
            if len(prices)<26: return 0,0,0
            e12 = ema(prices,12)
            e26 = ema(prices,26)
            # align
            diff = len(e12)-len(e26)
            macd_line = [e12[i+diff]-e26[i] for i in range(len(e26))]
            signal_line = ema(macd_line,9)
            diff2 = len(macd_line)-len(signal_line)
            histogram = [macd_line[i+diff2]-signal_line[i] for i in range(len(signal_line))]
            return round(macd_line[-1],2), round(signal_line[-1],2), round(histogram[-1],2)

        # Bollinger Bands (20,2)
        def calc_bb(prices, period=20):
            if len(prices)<period: return prices[-1],prices[-1],prices[-1]
            recent = prices[-period:]
            mid = sum(recent)/period
            std = math.sqrt(sum((x-mid)**2 for x in recent)/period)
            return round(mid-2*std,2), round(mid,2), round(mid+2*std,2)

        # EMA 20, 50, 200
        ema20 = ema(closes,20)[-1]
        ema50 = ema(closes,50)[-1] if len(closes)>=50 else closes[-1]
        ema200= ema(closes,200)[-1] if len(closes)>=200 else closes[-1]

        rsi = calc_rsi(closes)
        macd_val, macd_sig, macd_hist = calc_macd(closes)
        bb_low, bb_mid, bb_high = calc_bb(closes)

        price = closes[-1]
        prev_price = closes[-2] if len(closes)>1 else price
        chg_pct = (price-prev_price)/prev_price*100

        # Volume analysis
        avg_vol = sum(volumes[-20:])/20 if len(volumes)>=20 else volumes[-1]
        curr_vol = volumes[-1]
        vol_ratio = curr_vol/avg_vol if avg_vol else 1

        # ATR (14) for volatility
        def calc_atr(h,l,c,period=14):
            trs=[]
            for i in range(1,len(c)):
                tr=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
                trs.append(tr)
            if len(trs)<period: return trs[-1] if trs else 0
            atr=[sum(trs[:period])/period]
            for i in range(period,len(trs)):
                atr.append((atr[-1]*13+trs[i])/14)
            return round(atr[-1],2)

        atr = calc_atr(highs, lows, closes)

        # ── MARKET TREND PREDICTION ──
        bull_score = 0
        bear_score = 0
        signals = []

        # RSI signals
        if rsi < 30:
            bull_score += 2
            signals.append({"indicator":"RSI","value":rsi,"signal":"OVERSOLD — Strong Buy","color":"#69ff9c","detail":"RSI below 30 indicates oversold conditions. Stock likely to bounce."})
        elif rsi < 45:
            bull_score += 1
            signals.append({"indicator":"RSI","value":rsi,"signal":"Mildly Oversold — Lean Bullish","color":"#69ff9c","detail":"RSI approaching oversold. Momentum may shift bullish soon."})
        elif rsi > 70:
            bear_score += 2
            signals.append({"indicator":"RSI","value":rsi,"signal":"OVERBOUGHT — Strong Sell","color":"#ff6e6e","detail":"RSI above 70 indicates overbought conditions. Pullback likely."})
        elif rsi > 55:
            bear_score += 1
            signals.append({"indicator":"RSI","value":rsi,"signal":"Mildly Overbought — Lean Bearish","color":"#ffd966","detail":"RSI elevated. Momentum slowing, watch for reversal."})
        else:
            signals.append({"indicator":"RSI","value":rsi,"signal":"Neutral (30-55)","color":"#6b7280","detail":"RSI in neutral zone. No strong directional bias from momentum."})

        # MACD signals
        if macd_hist > 0 and macd_val > macd_sig:
            bull_score += 2
            signals.append({"indicator":"MACD","value":round(macd_hist,2),"signal":"BULLISH Crossover ↑","color":"#69ff9c","detail":"MACD above signal line with positive histogram. Upward momentum confirmed."})
        elif macd_hist > 0:
            bull_score += 1
            signals.append({"indicator":"MACD","value":round(macd_hist,2),"signal":"Weakly Bullish","color":"#69ff9c","detail":"Positive MACD histogram but crossover not confirmed yet."})
        elif macd_hist < 0 and macd_val < macd_sig:
            bear_score += 2
            signals.append({"indicator":"MACD","value":round(macd_hist,2),"signal":"BEARISH Crossover ↓","color":"#ff6e6e","detail":"MACD below signal line with negative histogram. Downward momentum confirmed."})
        else:
            bear_score += 1
            signals.append({"indicator":"MACD","value":round(macd_hist,2),"signal":"Weakly Bearish","color":"#ff9090","detail":"Negative MACD histogram. Bearish pressure present."})

        # EMA trend signals
        if price > ema20 > ema50:
            bull_score += 2
            signals.append({"indicator":"EMA Trend","value":round(ema20,2),"signal":"UPTREND — Price above EMA20 > EMA50","color":"#69ff9c","detail":"Price above both moving averages. Clear uptrend in place."})
        elif price > ema20:
            bull_score += 1
            signals.append({"indicator":"EMA Trend","value":round(ema20,2),"signal":"Short-term Bullish","color":"#69ff9c","detail":"Price above EMA20 but below EMA50. Short-term bullish, medium-term uncertain."})
        elif price < ema20 < ema50:
            bear_score += 2
            signals.append({"indicator":"EMA Trend","value":round(ema20,2),"signal":"DOWNTREND — Price below EMA20 < EMA50","color":"#ff6e6e","detail":"Price below both moving averages. Clear downtrend in place."})
        else:
            bear_score += 1
            signals.append({"indicator":"EMA Trend","value":round(ema20,2),"signal":"Short-term Bearish","color":"#ff9090","detail":"Price below EMA20. Short-term bearish pressure."})

        # EMA200 long term
        if price > ema200:
            bull_score += 1
            signals.append({"indicator":"Long-term Trend (EMA200)","value":round(ema200,2),"signal":"Above EMA200 — Long-term Bullish","color":"#69ff9c","detail":"Price above 200-day EMA. Long-term trend is bullish."})
        else:
            bear_score += 1
            signals.append({"indicator":"Long-term Trend (EMA200)","value":round(ema200,2),"signal":"Below EMA200 — Long-term Bearish","color":"#ff6e6e","detail":"Price below 200-day EMA. Long-term trend is bearish."})

        # Bollinger Band signals
        if price < bb_low:
            bull_score += 2
            signals.append({"indicator":"Bollinger Bands","value":round(bb_low,2),"signal":"Below Lower Band — Oversold","color":"#69ff9c","detail":"Price outside lower Bollinger Band. High probability mean-reversion bounce."})
        elif price > bb_high:
            bear_score += 2
            signals.append({"indicator":"Bollinger Bands","value":round(bb_high,2),"signal":"Above Upper Band — Overbought","color":"#ff6e6e","detail":"Price outside upper Bollinger Band. Pullback to mean likely."})
        elif price > bb_mid:
            bull_score += 1
            signals.append({"indicator":"Bollinger Bands","value":round(bb_mid,2),"signal":"Above Mid-Band — Mild Bullish","color":"#ffd966","detail":"Price in upper half of Bollinger Bands. Mild bullish momentum."})
        else:
            bear_score += 1
            signals.append({"indicator":"Bollinger Bands","value":round(bb_mid,2),"signal":"Below Mid-Band — Mild Bearish","color":"#ffd966","detail":"Price in lower half of Bollinger Bands. Mild bearish pressure."})

        # Volume signal
        if vol_ratio > 1.5 and chg_pct > 0:
            bull_score += 2
            signals.append({"indicator":"Volume","value":round(vol_ratio,1),"signal":"HIGH Volume Up-move — Strong Bullish","color":"#69ff9c","detail":f"Volume {vol_ratio:.1f}x above average on an up day. Institutional buying likely."})
        elif vol_ratio > 1.5 and chg_pct < 0:
            bear_score += 2
            signals.append({"indicator":"Volume","value":round(vol_ratio,1),"signal":"HIGH Volume Down-move — Strong Bearish","color":"#ff6e6e","detail":f"Volume {vol_ratio:.1f}x above average on a down day. Institutional selling likely."})
        elif vol_ratio > 1.2:
            signals.append({"indicator":"Volume","value":round(vol_ratio,1),"signal":"Above Average Volume","color":"#ffd966","detail":"Higher than normal volume. Move has conviction behind it."})
        else:
            signals.append({"indicator":"Volume","value":round(vol_ratio,1),"signal":"Below Average Volume","color":"#6b7280","detail":"Low volume. Move may lack conviction and reverse easily."})

        # Overall market prediction
        total = bull_score + bear_score
        bull_pct = round(bull_score/total*100) if total else 50

        if bull_score > bear_score+2:
            mkt_trend = "BULLISH"
            mkt_color = "#69ff9c"
            mkt_strength = "STRONG" if bull_score>bear_score+4 else "MODERATE"
            rec_strategy = "Long Call" if rsi<50 else "Bull Call Spread"
            mkt_detail = f"Technical indicators strongly favor upside. RSI={rsi}, MACD={'positive' if macd_hist>0 else 'negative'}, Price {'above' if price>ema50 else 'below'} EMA50."
        elif bear_score > bull_score+2:
            mkt_trend = "BEARISH"
            mkt_color = "#ff6e6e"
            mkt_strength = "STRONG" if bear_score>bull_score+4 else "MODERATE"
            rec_strategy = "Long Put" if rsi>50 else "Bear Put Spread"
            mkt_detail = f"Technical indicators favor downside. RSI={rsi}, MACD={'positive' if macd_hist>0 else 'negative'}, Price {'above' if price>ema50 else 'below'} EMA50."
        else:
            mkt_trend = "NEUTRAL"
            mkt_color = "#ffd966"
            mkt_strength = "WEAK"
            rec_strategy = "Iron Condor" if rsi>50 else "Calendar Spread"
            mkt_detail = f"Mixed signals. No clear directional bias. RSI={rsi} is neutral. Wait for confirmation."

        return {
            "symbol": symbol.replace('.NS',''),
            "price": round(price,2),
            "changePct": round(chg_pct,2),
            "rsi": rsi,
            "macd": macd_val, "macd_signal": macd_sig, "macd_hist": macd_hist,
            "ema20": round(ema20,2), "ema50": round(ema50,2), "ema200": round(ema200,2),
            "bb_low": bb_low, "bb_mid": bb_mid, "bb_high": bb_high,
            "atr": atr,
            "vol_ratio": round(vol_ratio,2),
            "bull_score": bull_score, "bear_score": bear_score, "bull_pct": bull_pct,
            "market_trend": mkt_trend,
            "market_color": mkt_color,
            "market_strength": mkt_strength,
            "market_detail": mkt_detail,
            "rec_strategy": rec_strategy,
            "signals": signals,
            "updatedAt": datetime.datetime.now().isoformat()
        }
    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()[:300]}



def fetch_oc_with_browser_cookies(clean, cookie_string):
    """Use cookies from user's browser to fetch NSE option chain."""
    import requests, datetime, time
    
    indices = ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"]
    is_index = clean in indices
    
    session = requests.Session()
    
    # Parse cookie string into dict
    if cookie_string:
        for c in cookie_string.split(";"):
            c = c.strip()
            if "=" in c:
                k, v = c.split("=", 1)
                session.cookies.set(k.strip(), v.strip())
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/option-chain",
        "X-Requested-With": "XMLHttpRequest",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    
    if is_index:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={clean}"
    else:
        url = f"https://www.nseindia.com/api/option-chain-equities?symbol={clean}"
    
    try:
        print(f"  [OC-COOKIE] Fetching {clean} with browser cookies...")
        r = session.get(url, headers=headers, timeout=20)
        print(f"  [OC-COOKIE] Status: {r.status_code}, Size: {len(r.content)}")
        
        if r.status_code == 200 and len(r.content) > 500:
            data = r.json()
            result = parse_nse_oc(data, clean)
            if 'error' not in result:
                result['source'] = 'NSE (Browser Cookies)'
                return result
        
        return {"error": f"NSE returned {r.status_code} with {len(r.content)} bytes. Please visit nseindia.com/option-chain first, then retry."}
    except Exception as e:
        return {"error": str(e)}


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
