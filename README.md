
# DotMatrixPy

Python製のゲームボーイ（DMG）エミュレータです。

## 使い方

ROMを指定して起動します。

```bash
python run_rom.py roms/your_rom.gb
```

## フォルダ構成

- `gb/` : エミュレータ本体
- `roms/` : 通常ROM
- `test_roms/` : テストROM（mooneye）
- `tests/` : テスト実行スクリプト

## テスト

Mooneyeテストを実行します。

```bash
python tests/run_mooneye.py
```

## 現在の状況

### Mooneye results

PASS: 61  FAIL: 50  TIMEOUT: 0  ERROR: 0
