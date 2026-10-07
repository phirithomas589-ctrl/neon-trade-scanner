import os, math
from datetime import datetime, timezone
from typing import Optional
import MetaTrader5 as mt5
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title='NeonTrade MT5 Opportunity Scanner', version='2.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

LIVE_TRADING = os.getenv('LIVE_TRADING', 'false').lower() == 'true'
DEFAULT_SYMBOLS = ['BTCUSD', 'XAUUSD', 'EURUSD', 'GBPUSD']
TIMEFRAME = mt5.TIMEFRAME_M15
BARS = 300

connection = {
    'login': int(os.getenv('MT5_LOGIN')) if os.getenv('MT5_LOGIN') else None,
    'password': os.getenv('MT5_PASSWORD'),
    'server': os.getenv('MT5_SERVER'),
    'path': os.getenv('MT5_PATH')
}

class BrokerConnectRequest(BaseModel):
    login: int
    password: str
    server: str
    path: Optional[str] = None


def ensure_mt5():
    path = connection.get('path')
    ok = mt5.initialize(path=path) if path else mt5.initialize()
    if not ok:
        raise HTTPException(503, f'MT5 initialize failed: {mt5.last_error()}')
    if connection.get('login') and connection.get('password') and connection.get('server'):
        if not mt5.login(int(connection['login']), password=connection['password'], server=connection['server']):
            raise HTTPException(401, f'MT5 login failed: {mt5.last_error()}')


def resolve_symbol(requested: str) -> str:
    requested = requested.upper()
    candidates = [requested]
    symbols = mt5.symbols_get()
    if symbols:
        names = [s.name for s in symbols]
        exact = next((n for n in names if n.upper() == requested), None)
        if exact: return exact
        pref = [n for n in names if n.upper().startswith(requested)]
        if pref: return pref[0]
        contains = [n for n in names if requested in n.upper()]
        if contains: return contains[0]
    raise HTTPException(404, f'MT5 symbol {requested} was not found. Check your broker symbol name.')


def ema_series(values, period):
    if len(values) < period: return []
    k = 2.0 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]: out.append(v * k + out[-1] * (1-k))
    return out


def rsi_series(values, period=14):
    if len(values) <= period: return []
    gains=[]; losses=[]
    for i in range(1,len(values)):
        d=values[i]-values[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    avg_gain=sum(gains[:period])/period; avg_loss=sum(losses[:period])/period
    out=[100 if avg_loss==0 else 100-(100/(1+avg_gain/avg_loss))]
    for i in range(period,len(gains)):
        avg_gain=((avg_gain*(period-1))+gains[i])/period
        avg_loss=((avg_loss*(period-1))+losses[i])/period
        out.append(100 if avg_loss==0 else 100-(100/(1+avg_gain/avg_loss)))
    return out


def atr(highs, lows, closes, period=14):
    trs=[]
    for i in range(1,len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    if len(trs)<period: return None
    return sum(trs[-period:])/period


def nearest_levels(highs, lows, price, window=120):
    hs=highs[-window:]; ls=lows[-window:]
    # Local swing points: 2 candles on either side.
    supports=[]; resistances=[]
    for i in range(2,len(hs)-2):
        if ls[i] < ls[i-1] and ls[i] < ls[i-2] and ls[i] <= ls[i+1] and ls[i] <= ls[i+2]:
            supports.append(ls[i])
        if hs[i] > hs[i-1] and hs[i] > hs[i-2] and hs[i] >= hs[i+1] and hs[i] >= hs[i+2]:
            resistances.append(hs[i])
    below=[x for x in supports if x < price]
    above=[x for x in resistances if x > price]
    support=max(below) if below else min(ls)
    resistance=min(above) if above else max(hs)
    return support, resistance


def analyze_symbol(requested: str):
    ensure_mt5()
    symbol=resolve_symbol(requested)
    if not mt5.symbol_select(symbol, True):
        raise HTTPException(404, f'Could not select {symbol}')
    rates=mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, BARS)
    if rates is None or len(rates)<100:
        raise HTTPException(502, f'Insufficient M15 candle data for {symbol}')
    closes=[float(x['close']) for x in rates]
    highs=[float(x['high']) for x in rates]
    lows=[float(x['low']) for x in rates]
    e20s=ema_series(closes,20); e50s=ema_series(closes,50)
    rsi=rsi_series(closes,14)[-1]
    ema20=e20s[-1]; ema50=e50s[-1]
    fast=ema_series(closes,12); slow=ema_series(closes,26)
    # Align MACD after slow EMA begins.
    macd_line=[]
    start=25
    for i in range(start,len(closes)):
        e12=ema_series(closes[:i+1],12)[-1]
        e26=ema_series(closes[:i+1],26)[-1]
        macd_line.append(e12-e26)
    signal_line=ema_series(macd_line,9)
    macd=macd_line[-1]; macd_signal=signal_line[-1]; macd_hist=macd-macd_signal
    a=atr(highs,lows,closes,14)
    tick=mt5.symbol_info_tick(symbol)
    if not tick or a is None: raise HTTPException(502, f'No live tick/ATR for {symbol}')
    bid=float(tick.bid); ask=float(tick.ask); spread=ask-bid; mid=(ask+bid)/2
    support,resistance=nearest_levels(highs,lows,mid)

    score=50; reasons=[]
    if ema20>ema50: score+=15; reasons.append('EMA20 > EMA50')
    else: score-=15; reasons.append('EMA20 < EMA50')
    if macd>macd_signal: score+=13; reasons.append('MACD bullish')
    else: score-=13; reasons.append('MACD bearish')
    if 50 <= rsi <= 70: score+=10; reasons.append('RSI bullish range')
    elif 30 <= rsi < 50: score-=5; reasons.append('RSI below 50')
    elif rsi>70: score-=6; reasons.append('RSI overbought')
    elif rsi<30: score+=6; reasons.append('RSI oversold')
    # Avoid calling a trade strong when spread is abnormally large vs ATR.
    spread_ratio=spread/a if a else 999
    if spread_ratio > 0.15:
        score-=12; reasons.append('Wide spread vs ATR')
    elif spread_ratio < 0.05:
        score+=4; reasons.append('Tight spread')

    score=max(0,min(100,round(score)))
    if score>=65: side='BUY'
    elif score<=35: side='SELL'
    else: side='NEUTRAL'

    entry=ask if side=='BUY' else bid if side=='SELL' else mid
    # Structure-aware stops/targets: use swing levels when sensible, otherwise ATR.
    if side=='BUY':
        sl=min(support, entry-1.5*a)
        risk=entry-sl
        tp=max(resistance, entry+2*risk)
        if tp<=entry: tp=entry+2*risk
    elif side=='SELL':
        sl=max(resistance, entry+1.5*a)
        risk=sl-entry
        tp=min(support, entry-2*risk)
        if tp>=entry: tp=entry-2*risk
    else:
        sl=entry-1.5*a; tp=entry+2*(1.5*a)
        risk=None

    digits=mt5.symbol_info(symbol).digits if mt5.symbol_info(symbol) else 5
    return {
        'requested_symbol':requested,'symbol':symbol,'timeframe':'M15','signal':side,
        'confidence':score,'entry':round(entry,digits),'stop_loss':round(sl,digits),'take_profit':round(tp,digits),
        'rsi':round(rsi,2),'ema20':round(ema20,digits),'ema50':round(ema50,digits),
        'macd':round(macd,digits+2),'macd_signal':round(macd_signal,digits+2),'macd_histogram':round(macd_hist,digits+2),
        'support':round(support,digits),'resistance':round(resistance,digits),'atr':round(a,digits),
        'spread':round(spread,digits),'reasons':reasons,'candles':len(rates),
        'timestamp':datetime.now(timezone.utc).isoformat()
    }

@app.get('/api/health')
def health():
    return {'ok':True,'live_trading':LIVE_TRADING,'scanner_symbols':DEFAULT_SYMBOLS,'timeframe':'M15'}

@app.post('/api/broker/connect')
def broker_connect(req: BrokerConnectRequest):
    global connection
    connection={'login':req.login,'password':req.password,'server':req.server,'path':req.path}
    ensure_mt5(); a=mt5.account_info()
    if a is None: raise HTTPException(502,'Connected to MT5 but account info is unavailable')
    return {'connected':True,'login':a.login,'server':a.server,'balance':a.balance,'equity':a.equity,'currency':a.currency,'trade_allowed':a.trade_allowed,'live_trading':LIVE_TRADING}

@app.get('/api/broker/status')
def broker_status():
    try:
        ensure_mt5(); a=mt5.account_info()
        return {'connected':a is not None,'login':a.login if a else None,'server':a.server if a else None,'balance':a.balance if a else None,'equity':a.equity if a else None,'currency':a.currency if a else None,'trade_allowed':a.trade_allowed if a else False,'live_trading':LIVE_TRADING}
    except Exception as e:
        return {'connected':False,'live_trading':LIVE_TRADING,'error':str(e)}

@app.get('/api/opportunities')
def opportunities(symbols: str=','.join(DEFAULT_SYMBOLS)):
    out=[]
    for requested in [x.strip().upper() for x in symbols.split(',') if x.strip()]:
        try: out.append(analyze_symbol(requested))
        except HTTPException as e: out.append({'requested_symbol':requested,'signal':'ERROR','error':e.detail})
    return {'scanned_at':datetime.now(timezone.utc).isoformat(),'timeframe':'M15','results':out}
