"""Client REST per l'API V3 ("Pro API") dei perpetual di Aster DEX.

Autenticazione: gli endpoint privati richiedono una firma EIP-712 prodotta
con la chiave privata dell'API wallet (il "signer") registrato su
https://www.asterdex.com/en/api-wallet. Il payload firmato e' la query string
dei parametri, con un nonce in microsecondi.

Documentazione: https://github.com/asterdex/api-docs
"""

import copy
import threading
import time
import urllib.parse

import requests
from eth_account import Account

try:  # eth-account >= 0.11
    from eth_account.messages import encode_typed_data

    def _encode(typed_data):
        return encode_typed_data(full_message=typed_data)
except ImportError:  # eth-account piu' vecchi
    from eth_account.messages import encode_structured_data

    def _encode(typed_data):
        return encode_structured_data(typed_data)


_TYPED_DATA_TEMPLATE = {
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "Message": [{"name": "msg", "type": "string"}],
    },
    "primaryType": "Message",
    "domain": {
        "name": "AsterSignTransaction",
        "version": "1",
        "chainId": 1666,
        "verifyingContract": "0x0000000000000000000000000000000000000000",
    },
    "message": {"msg": ""},
}

# Host alternativi per l'API V3: se uno risponde 403 (WAF) si prova l'altro
FALLBACK_HOSTS = ("https://fapi.asterdex.com", "https://fapi3.asterdex.com")

# User-Agent da browser: il firewall di Aster a volte blocca gli UA "da script"
_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "*/*",
}


class AsterAPIError(Exception):
    def __init__(self, status: int, code, msg: str):
        self.status = status
        self.code = code
        self.msg = msg
        super().__init__(f"Aster API error {code} (HTTP {status}): {msg}")


class AsterClient:
    def __init__(self, base_url: str, user: str = "", signer: str = "", private_key: str = "", timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.signer = signer
        self.private_key = private_key
        self.timeout = timeout
        self._session = requests.Session()
        self._nonce_lock = threading.Lock()
        self._last_nonce = 0
        self._hosts = [self.base_url] + [h for h in FALLBACK_HOSTS if h != self.base_url]

    # ------------------------------------------------------------------ utils

    def _nonce(self) -> int:
        """Timestamp in microsecondi, strettamente crescente."""
        with self._nonce_lock:
            nonce = int(time.time() * 1_000_000)
            if nonce <= self._last_nonce:
                nonce = self._last_nonce + 1
            self._last_nonce = nonce
            return nonce

    def _sign(self, params: dict) -> str:
        """Ritorna la query string firmata: '<params>&signature=0x...'."""
        params = {k: v for k, v in params.items() if v is not None}
        params["nonce"] = str(self._nonce())
        params["signer"] = self.signer
        if self.user:
            params["user"] = self.user
        query = urllib.parse.urlencode(params)

        typed_data = copy.deepcopy(_TYPED_DATA_TEMPLATE)
        typed_data["message"]["msg"] = query
        signed = Account.sign_message(_encode(typed_data), private_key=self.private_key)
        sig = signed.signature.hex()
        if not sig.startswith("0x"):
            sig = "0x" + sig
        return f"{query}&signature={sig}"

    def _handle(self, res: requests.Response):
        try:
            data = res.json()
        except ValueError:
            hint = ""
            if res.status_code == 403:
                hint = (" — accesso bloccato dal firewall di Aster (WAF): possibile "
                        "restrizione geografica o di rete, prova un'altra rete o una VPN")
            text = res.text.strip()[:150] or res.reason or "risposta non valida"
            raise AsterAPIError(res.status_code, None, text + hint)
        if isinstance(data, dict) and data.get("code", 0) not in (0, 200, None) and "msg" in data:
            raise AsterAPIError(res.status_code, data.get("code"), data.get("msg", ""))
        if res.status_code >= 400:
            raise AsterAPIError(res.status_code, None, str(data)[:200])
        return data

    def _request(self, method: str, path: str, query: str | None = None,
                 params: dict | None = None):
        """Esegue la richiesta provando gli host alternativi se uno e' bloccato."""
        last_exc: Exception | None = None
        for i, host in enumerate(self._hosts):
            url = host + path
            try:
                if method == "GET":
                    full = f"{url}?{query}" if query else url
                    res = self._session.get(full, params=params, headers=_HEADERS,
                                            timeout=self.timeout)
                else:
                    res = self._session.request(method, url, data=query,
                                                headers=_HEADERS, timeout=self.timeout)
                # 403/418/451 = bloccato da WAF/geo: prova l'host successivo
                if res.status_code in (403, 418, 451) and i < len(self._hosts) - 1:
                    last_exc = None
                    try:
                        self._handle(res)
                    except AsterAPIError as e:
                        last_exc = e
                    continue
                data = self._handle(res)
                if i > 0:  # ricorda l'host che funziona
                    self._hosts.insert(0, self._hosts.pop(i))
                return data
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                continue
        if last_exc:
            raise last_exc
        raise AsterAPIError(0, None, "nessun host API raggiungibile")

    def _public(self, path: str, params: dict | None = None):
        return self._request("GET", path, params=params or {})

    def _signed(self, method: str, path: str, params: dict | None = None):
        method = method.upper()
        if method not in ("GET", "POST", "PUT", "DELETE"):
            raise ValueError(f"metodo non supportato: {method}")
        body = self._sign(params or {})
        return self._request(method, path, query=body)

    # ----------------------------------------------------------- market data

    def ping(self):
        return self._public("/fapi/v3/ping")

    def server_time(self):
        return self._public("/fapi/v3/time")

    def exchange_info(self):
        return self._public("/fapi/v3/exchangeInfo")

    def klines(self, symbol: str, interval: str, limit: int = 200):
        return self._public("/fapi/v3/klines",
                            {"symbol": symbol, "interval": interval, "limit": limit})

    def ticker_price(self, symbol: str) -> float:
        data = self._public("/fapi/v3/ticker/price", {"symbol": symbol})
        return float(data["price"])

    def mark_price(self, symbol: str):
        return self._public("/fapi/v3/premiumIndex", {"symbol": symbol})

    # ---------------------------------------------------------- account/trade

    def balance(self):
        return self._signed("GET", "/fapi/v3/balance")

    def account(self):
        return self._signed("GET", "/fapi/v3/accountWithJoinMargin")

    def position_risk(self, symbol: str | None = None):
        return self._signed("GET", "/fapi/v3/positionRisk",
                            {"symbol": symbol} if symbol else {})

    def open_orders(self, symbol: str | None = None):
        return self._signed("GET", "/fapi/v3/openOrders",
                            {"symbol": symbol} if symbol else {})

    def income(self, income_type: str | None = None, start_time: int | None = None, limit: int = 1000):
        params = {"limit": limit}
        if income_type:
            params["incomeType"] = income_type
        if start_time:
            params["startTime"] = start_time
        return self._signed("GET", "/fapi/v3/income", params)

    def set_leverage(self, symbol: str, leverage: int):
        return self._signed("POST", "/fapi/v3/leverage",
                            {"symbol": symbol, "leverage": leverage})

    def set_margin_type(self, symbol: str, margin_type: str):
        """margin_type: ISOLATED oppure CROSSED. Ignora l'errore 'gia' impostato'."""
        try:
            return self._signed("POST", "/fapi/v3/marginType",
                                {"symbol": symbol, "marginType": margin_type})
        except AsterAPIError as e:
            if e.code == -4046:  # "No need to change margin type."
                return {}
            raise

    def new_order(self, symbol: str, side: str, order_type: str, **kwargs):
        params = {"symbol": symbol, "side": side, "type": order_type, **kwargs}
        return self._signed("POST", "/fapi/v3/order", params)

    def market_order(self, symbol: str, side: str, quantity: str, reduce_only: bool = False):
        params = {"quantity": quantity}
        if reduce_only:
            params["reduceOnly"] = "true"
        return self.new_order(symbol, side, "MARKET", **params)

    def stop_market_close(self, symbol: str, side: str, stop_price: str):
        """Stop-loss che chiude tutta la posizione al trigger."""
        return self.new_order(symbol, side, "STOP_MARKET",
                              stopPrice=stop_price, closePosition="true",
                              workingType="MARK_PRICE")

    def take_profit_market_close(self, symbol: str, side: str, stop_price: str):
        """Take-profit che chiude tutta la posizione al trigger."""
        return self.new_order(symbol, side, "TAKE_PROFIT_MARKET",
                              stopPrice=stop_price, closePosition="true",
                              workingType="MARK_PRICE")

    def cancel_all_orders(self, symbol: str):
        return self._signed("DELETE", "/fapi/v3/allOpenOrders", {"symbol": symbol})
