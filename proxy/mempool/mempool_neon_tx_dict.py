from typing import Dict, Deque, Union, Tuple, Optional
from collections import deque
from dataclasses import dataclass

import time
import math

from ..common_neon.errors import EthereumError
from ..common_neon.eth_proto import NeonTx


class MPTxDict:
    _life_time = 15

    @dataclass(frozen=True)
    class _Item:
        last_time: int
        neon_sig: str
        neon_tx: NeonTx
        error: Optional[EthereumError]

    def __init__(self):
        self._neon_tx_dict: Dict[str, MPTxDict._Item] = {}
        self._neon_tx_queue: Deque[MPTxDict._Item] = deque()

    @staticmethod
    def _get_time() -> int:
        return math.ceil(time.time())

    def add(self, neon_sig: str, neon_tx: NeonTx, exc: Optional[BaseException]) -> None:
        now = self._get_time()
        error = EthereumError(str(exc)) if exc is not None else None

        item = MPTxDict._Item(last_time=now, neon_sig=neon_sig, neon_tx=neon_tx, error=error)
        self._neon_tx_queue.append(item)
        self._neon_tx_dict[neon_sig] = item

    def get(self, neon_sig: str) -> Union[NeonTx, EthereumError, None]:
        item = self._neon_tx_dict.get(neon_sig, None)
        if item is None:
            return item
        if item.error is not None:
            return item.error
        return item.neon_tx

    def clear(self) -> None:
        if len(self._neon_tx_queue) == 0:
            return

        last_time = max(self._get_time() - self._life_time, 0)
        while (len(self._neon_tx_queue) > 0) and (self._neon_tx_queue[0].last_time < last_time):
            item = self._neon_tx_queue.popleft()
            self._neon_tx_dict.pop(item.neon_sig, None)
