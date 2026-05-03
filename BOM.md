# Bill of Materials — Sake Table (RPi Version)

**Project:** Sake Table
**Team:** Benjamin Lin, Anastasia Myers, Makenna Hull, Natalie Cupples
**Academic Year:** 2025-2026
**Last Revised:** 2026-02-09 (NC)

---

## Components

| Item | Manufacturer | Mfr Part # | Qty | Unit | Unit Cost | Total | Supplier | Supplier Part # |
|---|---|---|---|---|---|---|---|---|
| Load Cells (100 lb max) | TE Connectivity | FX293X-100A-0100-L | 4 | Each | $24.49 | $97.96 | DigiKey | 223-FX29K0-100A-0100-L-ND |
| Box Fan | Lasko | B20201 | 6 | Each | $26.98 | $161.88 | Home Depot | 399582 |
| Type K Thermocouples | Adafruit | 3245 | 6 | Each | $9.95 | $59.70 | DigiKey | 1188-SNS-TMP-DS18B20-MAXIM-ND |
| Raspberry Pi 4 Model B | Raspberry Pi | SC0194(9) | 1 | Each | $75.00 | $75.00 | DigiKey | 2648-SC0194(9)-ND |
| 24" HDMI Monitor | LG Electronics | 24U41YA-B | 1 | Each | $86.99 | $86.99 | Best Buy | 24U41YA-B |
| Dehumidifier | Energy Star | VGE033A3BS | 1 | Each | $269.99 | $269.99 | Vellgoo | VGE033A3BS |
| Load Cell Amplifier (HX711) | Avia Semiconductor | HX711 | 4 | Each | $11.50 | $46.00 | SparkFun | SEN-13879 |
| Humidity/Temp Sensor (SHT30) | DFRobot | SEN0137 | 1 | Each | $24.95 | $24.95 | DigiKey | 1528-4099-ND |
| HDMI Cable (micro-to-full) | Raspberry Pi | SC0546 | 1 | Each | $5.00 | $5.00 | DigiKey | 2648-SC0546-ND |
| 8-Channel Relay Module | SunFounder | TS0012 | 1 | Each | $11.87 | $11.87 | DigiKey | 4411-TS0012-ND |
| Raspberry Pi Power Supply | Raspberry Pi | SC1412 | 1 | Each | $8.00 | $8.00 | DigiKey | 2648-SC1412-ND |
| GPIO Breakout Board | Raspberry Pi | 2711 | 1 | Each | $19.95 | $19.95 | Adafruit | — |
| Thermocouple Amplifier (MAX31850K) | Adafruit | 1727 | 6 | Each | $14.95 | $89.70 | Adafruit | MAX31850K |

---

## Cost Summary

| Scenario | Total |
|---|---|
| Full build (all components) | **$956.99** |
| Reduced build (no monitor, 4 fans) | **$546.05** |

---

## Notes

- Load cells are rated to 100 lb each (4 total for platform weighing)
- Thermocouples are Type K, read via MAX31850K amplifiers on a shared 1-Wire bus (GPIO 4)
- Relay module is active-LOW (GPIO LOW = relay energized = fan ON)
- SHT30/SHT31 humidity sensor connects via I2C (GPIO 2/3)
- HX711 amplifiers connect via GPIO 5 (DAT) and GPIO 6 (CLK) for scale 1; scales 2-4 configurable
- Box fans are 20" Lasko units, one per fermentation zone, switched via relay channels
