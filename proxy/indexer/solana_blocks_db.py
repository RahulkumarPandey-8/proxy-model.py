import math
import logging

from typing import Optional, List, Any, Iterator

from ..common_neon.utils import SolBlockInfo
from ..indexer.base_db import BaseDB
from ..common_neon.config import Config


LOG = logging.getLogger(__name__)


class SolBlocksDB(BaseDB):
    _one_block_sec = 0.4

    def __init__(self, config: Config):
        super().__init__(
            table_name='solana_blocks',
            column_list=[
                'block_slot', 'block_hash', 'block_time', 'parent_block_slot', 'is_finalized', 'is_active'
            ]
        )
        self._config = config

    @staticmethod
    def _generate_fake_block_hash(block_slot: int) -> str:
        if block_slot < 0:
            return '0x' + '0' * 64

        hex_num = hex(block_slot)[2:]
        num_len = len(hex_num)
        hex_num = '00' + hex_num.rjust(((num_len >> 1) + (num_len % 2)) << 1, '0')
        return '0x' + hex_num.rjust(64, 'f')

    def _check_block_hash(self, block_slot: int, block_hash: Optional[str]) -> str:
        return block_hash or self._generate_fake_block_hash(block_slot)

    def _generate_fake_block_time(self, block_slot: int) -> int:
        # Search the nearest block before requested block
        request = f'''
           (SELECT block_slot AS b_block_slot,
                   block_time AS b_block_time,
                   NULL AS n_block_slot,
                   NULL AS n_block_time
              FROM {self._table_name}
             WHERE block_slot <= %s
          ORDER BY block_slot DESC LIMIT 1)

          UNION DISTINCT

          (SELECT NULL AS b_block_slot,
                  NULL AS b_block_time,
                  block_slot AS n_block_slot,
                  block_time AS n_block_time
              FROM {self._table_name}
             WHERE block_slot >= %s
          ORDER BY block_slot LIMIT 1)
        '''

        with self._conn.cursor() as cursor:
            cursor.execute(request, (block_slot, block_slot))
            value_list = cursor.fetchone()

        if value_list is None:
            LOG.warning(f'Failed to get nearest blocks for block {block_slot}. Calculate based on genesis')
            return math.ceil(block_slot * self._one_block_sec) + self._config.genesis_timestamp

        nearest_block_slot = value_list[0]
        if nearest_block_slot is not None:
            nearest_block_time = value_list[1]
            return nearest_block_time + math.ceil((block_slot - nearest_block_slot) * self._one_block_sec)

        nearest_block_slot = value_list[2]
        nearest_block_time = value_list[3]
        return nearest_block_time - math.ceil((nearest_block_slot - block_slot) * self._one_block_sec)

    def _check_block_time(self, block_slot: int, block_time: Optional[int]) -> int:
        return block_time or self._generate_fake_block_time(block_slot)

    @staticmethod
    def _get_fake_block_slot(hash_number: str) -> Optional[int]:
        hash_number = hash_number[2:].lstrip('f')
        if len(hash_number) > 12 or hash_number[:2] != '00':
            return None
        hex_number = hash_number.lstrip('0')
        if not hex_number:
            return 0
        return int(hex_number, 16)

    def _block_from_value(self, block_slot: Optional[int], value_list: Optional[List[Any]]) -> SolBlockInfo:
        if not value_list:
            if block_slot is None:
                return SolBlockInfo(block_slot=0)
            return SolBlockInfo(
                block_slot=block_slot,
                block_hash=self._generate_fake_block_hash(block_slot),
                block_time=self._generate_fake_block_time(block_slot),
                parent_block_hash=self._generate_fake_block_hash(block_slot-1),
            )

        if block_slot is None:
            block_slot = self._get_column_value('block_slot', value_list)
        return SolBlockInfo(
            block_slot=block_slot,
            block_hash=self._check_block_hash(block_slot, self._get_column_value('block_hash', value_list)),
            block_time=self._check_block_time(block_slot, self._get_column_value('block_time', value_list)),
            is_finalized=self._get_column_value('is_finalized', value_list),
            parent_block_hash=self._check_block_hash(block_slot - 1, value_list[6])
        )

    def _build_request(self) -> str:
        return f'''
                 SELECT {",".join(['a.' + c for c in self._column_list])},
                        b.block_hash AS parent_block_hash
                   FROM {self._table_name} AS a
        FULL OUTER JOIN {self._blocks_table_name} AS b
                     ON b.block_slot = a.block_slot - 1
                    AND a.is_active = True
                    AND b.is_active = True
        '''

    def get_block_by_slot(self, block_slot: int, latest_block_slot: int) -> SolBlockInfo:
        if block_slot > latest_block_slot:
            return SolBlockInfo(block_slot=block_slot)

        request = f'''
                (SELECT {",".join(['a.' + c for c in self._column_list])},
                        b.block_hash AS parent_block_hash
                   FROM {self._table_name} AS a
        LEFT OUTER JOIN {self._table_name} AS b
                     ON b.block_slot = %s
                    AND b.is_active = True
                  WHERE a.block_slot = %s
                    AND a.is_active = True
                  LIMIT 1)

         UNION DISTINCT

                (SELECT {",".join(['a.' + c for c in self._column_list])},
                        b.block_hash AS parent_block_hash
                   FROM {self._table_name} AS b
        LEFT OUTER JOIN {self._table_name} AS a
                     ON a.block_slot = %s
                    AND a.is_active = True
                  WHERE b.block_slot = %s
                    AND b.is_active = True
                  LIMIT 1)
                '''
        with self._conn.cursor() as cursor:
            cursor.execute(request, (block_slot - 1, block_slot, block_slot, block_slot - 1))
            return self._block_from_value(block_slot, cursor.fetchone())

    def get_block_by_hash(self, block_hash: str, latest_block_slot: int) -> SolBlockInfo:
        fake_block_slot = self._get_fake_block_slot(block_hash)
        if fake_block_slot is not None:
            block = self.get_block_by_slot(fake_block_slot, latest_block_slot)
            block.set_block_hash(block_hash)  # it can be a request from an uncle history branch
            return block

        request = f'''
                 SELECT {",".join(['a.' + c for c in self._column_list])},
                        b.block_hash AS parent_block_hash

                   FROM {self._table_name} AS a
        FULL OUTER JOIN {self._blocks_table_name} AS b
                     ON b.block_slot = a.block_slot - 1
                    AND a.is_active = True
                    AND b.is_active = True
                  WHERE a.block_hash = %s
        '''
        with self._conn.cursor() as cursor:
            cursor.execute(request, (block_hash,))
            return self._block_from_value(None, cursor.fetchone())

    def set_block_list(self, cursor: BaseDB.Cursor, iter_block: Iterator[SolBlockInfo]) -> None:
        value_list_list: List[List[Any]] = []
        for block in iter_block:
            value_list_list.append([
                block.block_slot, block.block_hash, block.block_time, block.parent_block_slot,
                block.is_finalized, block.is_finalized
            ])

        self._insert_batch(cursor, value_list_list)

    def finalize_block_list(self, cursor: BaseDB.Cursor, base_block_slot: int, block_slot_list: List[int]):
        cursor.execute(f'''
            UPDATE {self._table_name}
               SET is_finalized = True,
                   is_active = True
             WHERE block_slot IN ({','.join(["%s" for _ in block_slot_list])})
            ''',
            block_slot_list
        )

        cursor.execute(f'''
            DELETE FROM {self._table_name}
                  WHERE block_slot > %s
                    AND block_slot < %s
                    AND is_active = False
            ''',
            (base_block_slot, block_slot_list[-1])
        )

    def activate_block_list(self, cursor: BaseDB.Cursor, base_block_slot: int, block_slot_list: List[int]) -> None:
        cursor.execute(f'''
            UPDATE {self._table_name}
               SET is_active = False
             WHERE block_slot > %s
            ''',
            (base_block_slot,)
        )

        cursor.execute(f'''
            UPDATE {self._table_name}
               SET is_active = True
             WHERE block_slot IN ({','.join(["%s" for _ in block_slot_list])})
            ''',
            block_slot_list
        )
