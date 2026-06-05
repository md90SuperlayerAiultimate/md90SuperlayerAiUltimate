import os
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Optional, Tuple
import threading
import logging
import requests
import time

# ============================================================
# 0. SETUP LOGGING & ENVIRONMENT VALIDATION (FIX #2)
# ============================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def validate_environment():
    """FIX #2: Ensure critical API keys are set"""
    required_keys = ["BINANCE_API_KEY", "BINANCE_SECRET_KEY"]
    missing = [k for k in required_keys if not os.getenv(k)]
    
    if missing:
        raise ValueError(f"❌ Missing critical environment variables: {', '.join(missing)}")
    
    logger.info("✅ Environment validation passed")

# FIX #3: THREAD-SAFE CLIENT CACHING
_binance_client_cache = None
_client_lock = threading.Lock()

def get_binance_client():
    """Thread-safe Binance client getter"""
    global _binance_client_cache
    with _client_lock:
        if _binance_client_cache is None:
            try:
                from binance.client import Client
                api_key = os.getenv("BINANCE_API_KEY")
                api_secret = os.getenv("BINANCE_SECRET_KEY")
                _binance_client_cache = Client(api_key, api_secret)
                logger.info("✅ Binance client initialized")
            except ImportError:
                logger.error("❌ binance-python not installed: pip install python-binance")
        return _binance_client_cache

# ============================================================
# 1. DEFINISI ENUM & DATACLASS (Basis Data)
# ============================================================

class TradeAction(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

@dataclass
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

@dataclass
class MarketData:
    """Data dari chart + sumber real-time (GEX, COT, Order Book)"""
    symbol: str
    current_price: float
    candles: List[Candle]
    support: float
    resistance: float
    atr: float
    trend: str
    rsi: float
    stochastic_k: float
    stochastic_d: float
    volume_spike: bool
    vwap: float
    ema_8: float
    ema_21: float
    order_blocks: List[Dict]
    fvg_zones: List[Dict]
    swing_high: float
    swing_low: float
    
    # Real-Time Data
    gex_zero_gamma: float = 0.0
    gex_score: float = 0.0
    cot_managed_money_net_long: int = 0
    cot_bias: str = "neutral"
    order_book_bid_depth: float = 0.0
    order_book_ask_depth: float = 0.0
    vix: float = 15.0
    us10y: float = 4.5

@dataclass
class LayerVote:
    layer_name: str
    weight: int
    vote: TradeAction
    reason: str
    value: Optional[float] = None

@dataclass
class TradeSetup:
    action: TradeAction
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    tp4: float
    tp5: float
    tp6: float
    risk_reward: float
    fusion_score: float
    confidence: float
    mode: str
    strategy: str
    reasoning: str

# ============================================================
# 2. KERANGKA BERPIKIR (20 Layer + 5 Gates)
# ============================================================

class ThinkingFramework:
    """Mengubah Data Pasar menjadi Keputusan Arah (BUY/SELL/HOLD)"""
    
    def __init__(self, data: MarketData):
        self.data = data
        self.votes: List[LayerVote] = []

    def layer_1_snr(self) -> LayerVote:
        p = self.data.current_price
        s = self.data.support
        r = self.data.resistance
        if p <= s + 5:
            return LayerVote("SNR", 1, TradeAction.BUY, f"Dekat support {s:.2f}")
        if p >= r - 5:
            return LayerVote("SNR", 1, TradeAction.SELL, f"Dekat resistance {r:.2f}")
        return LayerVote("SNR", 1, TradeAction.HOLD, "Zona netral")

    def layer_2_ema(self) -> LayerVote:
        p = self.data.current_price
        e8 = self.data.ema_8
        e21 = self.data.ema_21
        if p > e8 > e21:
            return LayerVote("EMA", 1, TradeAction.BUY, f"Bullish alignment: {p:.2f} > {e8:.2f} > {e21:.2f}")
        if p < e8 < e21:
            return LayerVote("EMA", 1, TradeAction.SELL, f"Bearish alignment: {p:.2f} < {e8:.2f} < {e21:.2f}")
        return LayerVote("EMA", 1, TradeAction.HOLD, "EMA tidak sejajar")

    def layer_3_rsi(self) -> LayerVote:
        rsi = self.data.rsi
        if rsi < 35: return LayerVote("RSI", 1, TradeAction.BUY, f"RSI {rsi:.2f} < 35 (oversold)")
        if rsi > 65: return LayerVote("RSI", 1, TradeAction.SELL, f"RSI {rsi:.2f} > 65 (overbought)")
        return LayerVote("RSI", 1, TradeAction.HOLD, f"RSI {rsi:.2f} netral")

    def layer_4_stochastic(self) -> LayerVote:
        k = self.data.stochastic_k
        if k < 20: return LayerVote("STOCHASTIC", 1, TradeAction.BUY, f"%K {k:.2f} < 20")
        if k > 80: return LayerVote("STOCHASTIC", 1, TradeAction.SELL, f"%K {k:.2f} > 80")
        return LayerVote("STOCHASTIC", 1, TradeAction.HOLD, f"%K {k:.2f} netral")

    def layer_5_orderblock(self) -> LayerVote:
        p = self.data.current_price
        for ob in self.data.order_blocks:
            if ob["type"] == "bullish" and abs(p - ob["price"]) < 10:
                return LayerVote("ORDERBLOCK", 1, TradeAction.BUY, f"Bullish OB di {ob['price']:.2f}")
            if ob["type"] == "bearish" and abs(p - ob["price"]) < 10:
                return LayerVote("ORDERBLOCK", 1, TradeAction.SELL, f"Bearish OB di {ob['price']:.2f}")
        return LayerVote("ORDERBLOCK", 1, TradeAction.HOLD, "Tidak ada OB valid")

    def layer_6_volume(self) -> LayerVote:
        if self.data.volume_spike and self.data.candles:
            last = self.data.candles[-1]
            if last.close > last.open:
                return LayerVote("VOLUME", 1, TradeAction.BUY, "Volume spike + bullish close")
            if last.close < last.open:
                return LayerVote("VOLUME", 1, TradeAction.SELL, "Volume spike + bearish close")
        return LayerVote("VOLUME", 1, TradeAction.HOLD, "Tidak ada volume spike")

    def layer_7_mtf(self) -> LayerVote:
        trend = self.data.trend
        if trend == "BULLISH": return LayerVote("MTF", 1, TradeAction.BUY, "Mayoritas TF bullish")
        if trend == "BEARISH": return LayerVote("MTF", 1, TradeAction.SELL, "Mayoritas TF bearish")
        return LayerVote("MTF", 1, TradeAction.HOLD, "Trend netral")

    def layer_8_sentimen(self) -> LayerVote:
        last = self.data.candles[-1] if self.data.candles else None
        if last:
            delta = last.close - last.open
            if delta > 0: return LayerVote("SENTIMEN", 1, TradeAction.BUY, f"Delta positif +{delta:.2f}")
            if delta < 0: return LayerVote("SENTIMEN", 1, TradeAction.SELL, f"Delta negatif {delta:.2f}")
        return LayerVote("SENTIMEN", 1, TradeAction.HOLD, "Sentimen netral")

    def layer_9_ultrascalper(self) -> LayerVote:
        if self.data.ema_8 > self.data.ema_21:
            return LayerVote("UltraScalper", 1, TradeAction.BUY, "EMA8 > EMA21 (bullish scalping)")
        if self.data.ema_8 < self.data.ema_21:
            return LayerVote("UltraScalper", 1, TradeAction.SELL, "EMA8 < EMA21 (bearish scalping)")
        return LayerVote("UltraScalper", 1, TradeAction.HOLD, "EMA8 = EMA21")

    def layer_10_primeswing(self) -> LayerVote:
        swings = [c.close for c in self.data.candles[-20:]]
        if len(swings) < 5: return LayerVote("PrimeSwing", 2, TradeAction.HOLD, "Data tidak cukup")
        recent_high = max(swings[-5:])
        recent_low = min(swings[-5:])
        prev_high = max(swings[-10:-5])
        prev_low = min(swings[-10:-5])
        if recent_high > prev_high and recent_low > prev_low:
            return LayerVote("PrimeSwing", 2, TradeAction.BUY, "HH/HL (bullish BOS)")
        if recent_high < prev_high and recent_low < prev_low:
            return LayerVote("PrimeSwing", 2, TradeAction.SELL, "LH/LL (bearish BOS)")
        return LayerVote("PrimeSwing", 2, TradeAction.HOLD, "Tidak ada BOS")

    def layer_11_macroflow(self) -> LayerVote:
        p = self.data.current_price
        for fvg in self.data.fvg_zones:
            if fvg["type"] == "bullish" and fvg["bottom"] <= p <= fvg["top"]:
                return LayerVote("MacroFlow", 2, TradeAction.BUY, "Bullish FVG aktif")
            if fvg["type"] == "bearish" and fvg["bottom"] <= p <= fvg["top"]:
                return LayerVote("MacroFlow", 2, TradeAction.SELL, "Bearish FVG aktif")
        if self.data.trend == "BULLISH": return LayerVote("MacroFlow", 2, TradeAction.BUY, "Tren utama bullish")
        if self.data.trend == "BEARISH": return LayerVote("MacroFlow", 2, TradeAction.SELL, "Tren utama bearish")
        return LayerVote("MacroFlow", 2, TradeAction.HOLD, "Tidak ada konfirmasi macro")

    def layer_12_vwap(self) -> LayerVote:
        p = self.data.current_price
        v = self.data.vwap
        if p < v and p > v * 0.99: return LayerVote("VWAP-Z", 1, TradeAction.BUY, f"Harga {p:.2f} < VWAP {v:.2f}")
        if p > v and p < v * 1.01: return LayerVote("VWAP-Z", 1, TradeAction.SELL, f"Harga {p:.2f} > VWAP {v:.2f}")
        return LayerVote("VWAP-Z", 1, TradeAction.HOLD, "Harga jauh dari VWAP")

    def layer_13_atr(self) -> LayerVote:
        return LayerVote("ATR", 1, TradeAction.HOLD, f"ATR = {self.data.atr:.2f}", self.data.atr)

    def layer_14_ote_fibonacci(self) -> LayerVote:
        high = self.data.swing_high
        low = self.data.swing_low
        if high == low: return LayerVote("OTE FIBONACCI", 1, TradeAction.HOLD, "Range tidak valid")
        p = self.data.current_price
        if high > low:
            fib618 = low + 0.618 * (high - low)
            fib786 = low + 0.786 * (high - low)
            if fib618 <= p <= fib786: return LayerVote("OTE FIBONACCI", 1, TradeAction.BUY, f"Di OTE {fib618:.2f}-{fib786:.2f}")
        else:
            fib618 = high - 0.618 * (high - low)
            fib786 = high - 0.786 * (high - low)
            if fib786 <= p <= fib618: return LayerVote("OTE FIBONACCI", 1, TradeAction.SELL, f"Di OTE {fib786:.2f}-{fib618:.2f}")
        return LayerVote("OTE FIBONACCI", 1, TradeAction.HOLD, "Harga di luar OTE")

    def layer_15_idm_fvg(self) -> LayerVote:
        if len(self.data.candles) < 6: return LayerVote("IDM/FVG", 1, TradeAction.HOLD, "Data tidak cukup")
        recent_low = min(c.low for c in self.data.candles[-3:])
        prev_low = min(c.low for c in self.data.candles[-6:-3])
        recent_high = max(c.high for c in self.data.candles[-3:])
        prev_high = max(c.high for c in self.data.candles[-6:-3])
        if recent_low < prev_low and self.data.current_price > recent_low + 5:
            return LayerVote("IDM/FVG", 1, TradeAction.BUY, f"Sweep low {prev_low:.2f} -> {recent_low:.2f}")
        if recent_high > prev_high and self.data.current_price < recent_high - 5:
            return LayerVote("IDM/FVG", 1, TradeAction.SELL, f"Sweep high {prev_high:.2f} -> {recent_high:.2f}")
        return LayerVote("IDM/FVG", 1, TradeAction.HOLD, "Tidak ada sweep")

    def layer_16_mastercall(self) -> LayerVote:
        if len(self.data.candles) < 2: return LayerVote("MasterCall", 1, TradeAction.HOLD, "Data tidak cukup")
        last = self.data.candles[-1]
        prev = self.data.candles[-2]
        if last.close > last.open and last.close > prev.high and last.open < prev.low:
            return LayerVote("MasterCall", 1, TradeAction.BUY, f"Bullish engulfing close {last.close:.2f}")
        if last.close < last.open and last.close < prev.low and last.open > prev.high:
            return LayerVote("MasterCall", 1, TradeAction.SELL, f"Bearish engulfing close {last.close:.2f}")
        body = abs(last.close - last.open)
        wick_up = last.high - max(last.close, last.open)
        wick_down = min(last.close, last.open) - last.low
        if wick_up > 2 * body and wick_down < body * 0.3:
            return LayerVote("MasterCall", 1, TradeAction.SELL, f"Bearish pinbar di {last.high:.2f}")
        if wick_down > 2 * body and wick_up < body * 0.3:
            return LayerVote("MasterCall", 1, TradeAction.BUY, f"Bullish pinbar di {last.low:.2f}")
        return LayerVote("MasterCall", 1, TradeAction.HOLD, "Tidak ada pola kandidat")

    def layer_17_time_stop(self) -> LayerVote:
        return LayerVote("TIME STOP", 0, TradeAction.HOLD, "Belum aktif (pre-trade)")

    def layer_18_gex(self) -> LayerVote:
        """Gamma Exposure: Negative Gamma = Amplified Trend"""
        cp = self.data.current_price
        zero = self.data.gex_zero_gamma
        score = self.data.gex_score
        if cp < zero and score < -0.5:
            return LayerVote("GEX", 2, TradeAction.SELL, f"Negative gamma: price {cp:.0f} < zero {zero:.0f}")
        if cp > zero and score > 0.5:
            return LayerVote("GEX", 2, TradeAction.BUY, f"Positive gamma: price {cp:.0f} > zero {zero:.0f}")
        return LayerVote("GEX", 2, TradeAction.HOLD, "Gamma netral")

    def layer_19_cot(self) -> LayerVote:
        """COT: Managed Money extreme long + price dropping = distribution"""
        bias = self.data.cot_bias
        net_long = self.data.cot_managed_money_net_long
        if bias == "contrarian_bearish" and net_long > 80000:
            return LayerVote("COT", 2, TradeAction.SELL, f"MM net-long {net_long} (distribution)")
        if bias == "contrarian_bullish":
            return LayerVote("COT", 2, TradeAction.BUY, f"MM extreme short (accumulation)")
        return LayerVote("COT", 2, TradeAction.HOLD, "COT netral")

    def layer_20_orderbook(self) -> LayerVote:
        """Order Book Depth: Asymmetric pressure detection"""
        bid = self.data.order_book_bid_depth
        ask = self.data.order_book_ask_depth
        if ask > bid * 1.5:
            return LayerVote("ORDERBOOK", 1, TradeAction.SELL, f"Ask depth {ask:.1f} > bid depth {bid:.1f}")
        if bid > ask * 1.5:
            return LayerVote("ORDERBOOK", 1, TradeAction.BUY, f"Bid depth {bid:.1f} > ask depth {ask:.1f}")
        return LayerVote("ORDERBOOK", 1, TradeAction.HOLD, "Order book balanced")

    def get_all_votes(self) -> List[LayerVote]:
        return [
            self.layer_1_snr(), self.layer_2_ema(), self.layer_3_rsi(), self.layer_4_stochastic(),
            self.layer_5_orderblock(), self.layer_6_volume(), self.layer_7_mtf(), self.layer_8_sentimen(),
            self.layer_9_ultrascalper(), self.layer_10_primeswing(), self.layer_11_macroflow(), self.layer_12_vwap(),
            self.layer_13_atr(), self.layer_14_ote_fibonacci(), self.layer_15_idm_fvg(), self.layer_16_mastercall(),
            self.layer_17_time_stop(), self.layer_18_gex(), self.layer_19_cot(), self.layer_20_orderbook()
        ]

    def validate_gates(self, votes: List[LayerVote]) -> Tuple[bool, TradeAction]:
        """Gate 1-5 berurutan"""
        vote_dict = {v.layer_name: v for v in votes}
        
        dir1 = self._get_direction(vote_dict, ["EMA", "MTF", "PrimeSwing"])
        if dir1 == TradeAction.HOLD: return False, TradeAction.HOLD
        
        dir2 = self._get_direction(vote_dict, ["OTE FIBONACCI", "IDM/FVG", "GEX", "ORDERBOOK"])
        if dir2 == TradeAction.HOLD: return False, TradeAction.HOLD
        
        momentum_layers = ["RSI", "STOCHASTIC", "VOLUME", "VWAP-Z", "COT"]
        actions = [vote_dict[name].vote for name in momentum_layers if name in vote_dict]
        if actions.count(TradeAction.BUY) < 3 and actions.count(TradeAction.SELL) < 3:
            return False, TradeAction.HOLD
        
        master = vote_dict.get("MasterCall")
        ultra = vote_dict.get("UltraScalper")
        if not master or master.vote == TradeAction.HOLD:
            return False, TradeAction.HOLD
        if ultra and ultra.vote != master.vote and ultra.vote != TradeAction.HOLD:
            return False, TradeAction.HOLD
        
        if self.data.vix > 25 or self.data.atr > 2000:
            return False, TradeAction.HOLD
        
        return True, master.vote

    def _get_direction(self, vote_dict, names):
        buy = sum(1 for n in names if n in vote_dict and vote_dict[n].vote == TradeAction.BUY)
        sell = sum(1 for n in names if n in vote_dict and vote_dict[n].vote == TradeAction.SELL)
        return TradeAction.BUY if buy > sell else TradeAction.SELL if sell > buy else TradeAction.HOLD

# ============================================================
# 3. LOGIKA EKSEKUSI
# ============================================================

class ExecutionLogic:
    """Menghitung parameter entry, SL, TP, dan Risk-Reward"""
    
    def __init__(self, data: MarketData, direction: TradeAction):
        self.data = data
        self.direction = direction

    def calculate_entry(self) -> float:
        cp = self.data.current_price
        zero = self.data.gex_zero_gamma
        if self.direction == TradeAction.SELL:
            if cp < zero:
                return cp * 0.998
            return self.data.resistance - 50
        else:
            if cp > zero:
                return cp * 1.002
            return self.data.support + 50

    def calculate_sl(self, entry: float) -> float:
        atr = self.data.atr
        if self.direction == TradeAction.SELL:
            swing = self.data.swing_high
            return max(swing + 0.3 * atr, entry + 200)
        else:
            swing = self.data.swing_low
            return min(swing - 0.3 * atr, entry - 200)

    def calculate_tp_grid(self, entry: float, sl: float) -> Tuple[float, float, float]:
        risk = abs(entry - sl)
        if self.direction == TradeAction.SELL:
            tp1 = self.data.support if self.data.support < entry else entry - risk
            tp2 = tp1 - risk
            tp3 = tp2 - risk
        else:
            tp1 = self.data.resistance if self.data.resistance > entry else entry + risk
            tp2 = tp1 + risk
            tp3 = tp2 + risk
        return tp1, tp2, tp3

    def calculate_rr(self, entry: float, sl: float, tp1: float) -> float:
        """FIX #4: Correct Risk-Reward calculation"""
        risk = abs(entry - sl)
        reward = abs(tp1 - entry)
        return reward / risk if risk > 0 else 0.0

    def generate_setup(self) -> TradeSetup:
        entry = self.calculate_entry()
        sl = self.calculate_sl(entry)
        tp1, tp2, tp3 = self.calculate_tp_grid(entry, sl)
        rr = self.calculate_rr(entry, sl, tp1)
        return TradeSetup(
            action=self.direction, entry=entry, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
            tp4=0.0, tp5=0.0, tp6=0.0, risk_reward=rr, fusion_score=0.85,
            confidence=0.9 if rr >= 2.0 else 0.6,
            mode="🔥 ALL-IN HYBRID" if rr >= 2.0 else "⚡ SCALPING",
            strategy="[SMC + GEX] Institutional Confluence",
            reasoning=f"Entry {entry:.2f}, SL {sl:.2f}, TP1 {tp1:.2f} (RR 1:{rr:.2f})"
        )

# ============================================================
# 4. MAIN SYSTEM: PHANTOM GENESIS SEQUENTIAL
# ============================================================

class PhantomGenesisSequential:
    """Menghubungkan Kerangka Berpikir → Logika Eksekusi"""
    
    def __init__(self, market_data: MarketData):
        self.data = market_data

    def run(self) -> Dict:
        thinker = ThinkingFramework(self.data)
        votes = thinker.get_all_votes()
        
        total_weight = sum(v.weight for v in votes if v.vote != TradeAction.HOLD)
        buy_weight = sum(v.weight for v in votes if v.vote == TradeAction.BUY)
        sell_weight = sum(v.weight for v in votes if v.vote == TradeAction.SELL)
        if buy_weight > sell_weight:
            fusion_score = buy_weight / (buy_weight + sell_weight) if (buy_weight + sell_weight) > 0 else 0
            primary_dir = TradeAction.BUY
        elif sell_weight > buy_weight:
            fusion_score = sell_weight / (buy_weight + sell_weight) if (buy_weight + sell_weight) > 0 else 0
            primary_dir = TradeAction.SELL
        else:
            fusion_score = 0.5
            primary_dir = TradeAction.HOLD
        
        passed, gate_dir = thinker.validate_gates(votes)
        if not passed or primary_dir == TradeAction.HOLD:
            return {
                "status": "HOLD", "fusion_score": round(fusion_score, 2),
                "reason": "Gates failed or no clear direction",
                "action": "HOLD", "entry": 0.0, "sl": 0.0, "tp": 0.0, "confidence": 0.0
            }
        
        executor = ExecutionLogic(self.data, gate_dir)
        setup = executor.generate_setup()
        setup.fusion_score = fusion_score
        
        return {
            "reflection": "Analisis 20 layer + 5 gates dengan data real-time (GEX, COT, Order Book).",
            "symbol": self.data.symbol, "strategy": setup.strategy, "reasoning": setup.reasoning,
            "action": setup.action.value, "entry": round(setup.entry, 2),
            "sl": round(setup.sl, 2), "tp": round(setup.tp1, 2), "confidence": round(setup.confidence, 2)
        }

# ============================================================
# 5. HELPER FUNCTIONS (FIX #5, #6)
# ============================================================

def append_jsonl(path: str, obj: Dict, max_retries: int = 3) -> bool:
    """FIX #6: Append to JSONL file safely"""
    for attempt in range(max_retries):
        try:
            with open(path, "a") as f:
                json.dump(obj, f)
                f.write("\n")
            return True
        except IOError as e:
            if attempt == max_retries - 1:
                logger.error(f"❌ Failed to append JSONL: {e}")
                return False
            time.sleep(0.5 ** attempt)
    return False

def dex_trending(max_retries: int = 3) -> Optional[Dict]:
    """FIX #5: Fetch DEX trending with robust error handling"""
    DEXSCREENER_TRENDING = "https://api.dexscreener.com/latest/dex/trending"
    
    for attempt in range(max_retries):
        try:
            r = requests.get(DEXSCREENER_TRENDING, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                logger.error(f"❌ DEX API failed after {max_retries} retries: {e}")
                return None
            wait_time = 2 ** attempt
            logger.warning(f"⚠️ DEX API error, retrying in {wait_time}s...")
            time.sleep(wait_time)
    return None

# ============================================================
# 6. CONTOH EKSEKUSI
# ============================================================

def create_dummy_data_with_realtime() -> MarketData:
    candles = [Candle("12:15", 4556.44, 4557.07, 4553.84, 4554.27, 100),
               Candle("12:20", 4554.26, 4556.96, 4552.77, 4556.57, 150),
               Candle("12:25", 4556.52, 4560.81, 4556.06, 4560.39, 200),
               Candle("12:30", 4560.44, 4564.07, 4559.80, 4564.05, 180),
               Candle("12:35", 4564.00, 4570.06, 4563.18, 4568.57, 250)]
    return MarketData(
        symbol="XAUUSD", current_price=4564.35, candles=candles,
        support=4552.77, resistance=4570.06, atr=27.5, trend="BULLISH",
        rsi=58.2, stochastic_k=65.4, stochastic_d=60.1, volume_spike=False,
        vwap=4560.20, ema_8=4562.80, ema_21=4558.30,
        order_blocks=[{"type": "bullish", "price": 4555.00}],
        fvg_zones=[{"type": "bullish", "top": 4562.00, "bottom": 4558.00}],
        swing_high=4570.06, swing_low=4552.77,
        gex_zero_gamma=4567.00, gex_score=-0.82,
        cot_managed_money_net_long=90000, cot_bias="contrarian_bearish",
        order_book_bid_depth=450.0, order_book_ask_depth=850.0, vix=21.5, us10y=4.58
    )

if __name__ == "__main__":
    try:
        validate_environment()
    except ValueError as e:
        logger.error(str(e))
        exit(1)
    
    data = create_dummy_data_with_realtime()
    system = PhantomGenesisSequential(data)
    result = system.run()
    
    print("\n=== OUTPUT FINAL (JSON) ===")
    print(json.dumps(result, indent=2))
    
    if result["action"] != "HOLD":
        print(f"\n🚀 EKSEKUSI: {result['action']} di {result['entry']}, SL {result['sl']}, TP {result['tp']}")
    else:
        print(f"\n⏸️ HOLD: {result['reason']}")
