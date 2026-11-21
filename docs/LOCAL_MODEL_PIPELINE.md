# Локальная модель для анализа автомобилей

Установите зависимости для CPU PyTorch:

```powershell
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install transformers safetensors huggingface_hub
```

Запуск. При первом запуске автоматически скачаются веса небольшой instruct-модели
`Qwen/Qwen2.5-0.5B-Instruct` и закэшируются в Hugging Face cache:

```powershell
python tools/auto_label_car.py car.glb --make Kia --model Sportage --year 2023 --out car_review --ollama
```

Результат: `car_parts.glb`, `part_review.json`, `vehicle_data.json` и
`review_report.md`. Без `--ollama` работают только геометрические эвристики.
Локальная модель на PyTorch/Transformers получает признаки и примеры из `tools/part_examples.jsonl`,
но не технические факты. Автомобильные данные берутся отдельно из NHTSA vPIC;
если API не подтверждает поле, оно остаётся `unknown`.
