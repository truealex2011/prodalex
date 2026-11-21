"""Собирает data/catalog/sketchfab_models.json — реестр 3D-моделей, которые команда выбрала на Sketchfab.

Данные получены разбором присланных списков ссылок skfb.ly (короткая ссылка -> uid -> публичный API Sketchfab).
Реестр хранится в репозитории, а в БД попадает командой `python tools/import_models.py`.
Правило допуска к хранению в S3: лицензия Creative Commons без ND (кузов перекрашивается) и модель скачиваемая.
"""
import json
import re
from pathlib import Path

# короткий код | uid | название | лицензия | скачиваема | полигонов | автор
RAW = """
pMwP8|a57b6861ac164e10847861c5b9a30ca3|2017 Mazda CX-5|CC Attribution|1|3517961|Res1n
pwLqG|236a5a6e2fa6420fbdf641f4800cd544|Toyota Camry 2020|CC Attribution|1|176719|ItsDiyor
pGuFy|b89d15a4b99349d78c06dcc38e79fd60|2023 Kia Sportage GT-Line|CC Attribution-NonCommercial-ShareAlike|1|587223|Ddiaz Design
6S7pA|1b3088c59bd948e5b2299186f4cc04c9|Kia Optima 2019|Editorial|0|1258088|SQUIR3D
6WCzy|92ca58712c854a3b911d1105e96d8f5c|Kia Rio 2021|Editorial|0|1426281|SQUIR3D
pBQ7N|c3bf8357b4b349349f3414dca2dc70fb|2022 Kia Ceed Sportswagon GT-PlusLine|CC Attribution-NonCommercial-ShareAlike|1|565354|Ddiaz Design
ptXGK|3cc28d4cf5574383b7dc030638ee6199|2023 Hyundai Creta|CC Attribution-NonCommercial|1|267492|Ddiaz Design
ptJ8y|7e3ef2b4aa2a424eb0f0b05607a99bb3|2022 Hyundai Elantra N|CC Attribution-NonCommercial|1|251445|Ddiaz Design
pGIZv|6e69767042b2453ea91d533076821636|2022 Hyundai Tucson L 270T|CC Attribution-NonCommercial-ShareAlike|1|331797|Ddiaz Design
pswQp|22809755210348ca89bd450231f53ca2|2017 Skoda Octavia RS||0|440221|Ddiaz Design
psxpQ|40dc7c8a08e1479ba3bbd93d06744ede|2020 Skoda Rapid|CC Attribution-NonCommercial-ShareAlike|1|107260|Ddiaz Design
ppCqW|cec6b04b06724f1088129c1054ccb1ec|Hyundai Accent 2013|CC Attribution|1|951346|L95XP
p9TCY|c9b7cf9c176b458785edd0b12e235364|Audi A6 C8 Limousine|Free Standard|1|301880|Gerda
oIVvH|72b41da7f1304d2ca51e5fcff5ff71d4|Porsche Cayenne||0|2073132|MotorsConic
pwLrF|6d7d34ee42734d1ab28a6b1f1c5fc4fc|Toyota Corolla 2020|CC Attribution|1|205990|ItsDiyor
oFVRS|609ab2b8b1554611b0ff973d7673179a|Toyota RAV4||0|1947958|masoomalis341
6WBsw|b7df62bb069c44a18cebdeaec1d41a03|Toyota Land Cruiser Prado 2021|Editorial|0|1422427|SQUIR3D
6ZZPz|2dc0119368f24f84a8e840934143499c|Mazda 6 Wagon 2020|Editorial|0|1398674|SQUIR3D
pABqx|f9f2cbdacac74ee7acfe1c731195ccaa|2023 Nissan X-Trail 4x4-i|CC Attribution|1|199109|Ddiaz Design
p8MET|ef3c5a9808b44b44ac600ad4431b8680|2018 Nissan Qashqai|CC Attribution-NonCommercial-ShareAlike|1|154936|Ddiaz Design
o6nQt|7b410046e8994094a1a54f8daca1c4b6|Honda CR-V LX 2021|Editorial|0|1383535|SQUIR3D
pOMxy|6347047d7187409aa85df73559f5298e|2026 Mitsubishi Outlander ES|CC Attribution|1|964944|twr422
psIDs|7e0130019e1b4feea72f2daa2c0cfd78|2021 Volkswagen Polo Plus|CC Attribution-NonCommercial-ShareAlike|1|213991|Ddiaz Design
prHut|27e25fcead154e62a1dd8e92ccedb691|2016 Ford Focus RS|CC Attribution-NonCommercial-ShareAlike|1|85963|Ddiaz Design
oRzFW|c11b4bf29e92498a9b714e858274e810|Renault Logan 2010|Standard|0|1336689|Gov3dstudio
o88nC|997ae3649b2041d5a76c3e9096c4a07c|Renault Duster 2018||0|765959|podshyvalov.htc
pswoT|6465b6ee36e24233b5b269b99471f8ef|2021 BMW 3 Series 325LI|CC Attribution-NonCommercial-ShareAlike|1|210661|Ddiaz Design
pFqUW|136ecd58732b4f88b0bcd3cb74a71e54|2022 Mercedes-Benz E-Class AMG Line|CC Attribution|1|474610|tonielpro520
pPxHK|4200138ec0b640b98defde1481bfe165|Geely Coolray 2024|Free Standard|1|1384059|geyges
oPtTK|539b2257cb504e53969a8c38c1a686cc|Nissan Cefiro A31|CC Attribution|1|203723|Asadawut.Kaewma
oOs7o|31201f80892b404a8e49c272490a664c|LADA 2107|CC Attribution|1|105221|tiunov.se
oQXHB|6622502050b548ef85796886f319dc99|Lada (VAZ) 2110|Free Standard|1|428990|nyranarod
oK8yM|478330312c3d46fd94999c3133ce0366|BMW 3 Series Touring (E46) 2001|CC Attribution|1|1194461|Luquita
pypUs|e10068b2511b4ed7b724a8d87a362727|Toyota bZ4X|Free Standard|1|432266|Gerda
pDUSE|9f48fc0a66e44a69a09fda2f864e5944|Toyota Crown 2025|CC Attribution|1|2022116|sultan
ptJLz|4ba1b1b0eb844e318cc708ada1f2f51f|2024 Hyundai Elantra N|CC Attribution-NonCommercial-ShareAlike|1|258192|Ddiaz Design
ptJxU|fa88bbdbe0df406b8eab167b9591d2f2|2020 Hyundai ix25 / 2022 Hyundai Creta|CC Attribution-NonCommercial|1|90369|Ddiaz Design
pIPXy|046b825f3cd64559a3c1a5430b4fe105|2021 Hyundai Mistra 270T|CC Attribution-NonCommercial-ShareAlike|1|368248|Ddiaz Design
p8nGQ|05e7604a0f4643ab87c141e244813e9a|Nissan 350Z|CC Attribution-NonCommercial-ShareAlike|1|46258|karaman.arman
pApsO|98a84407998a4bc6b4dee912b289f15b|Kia Sportage|CC Attribution|1|374469|Ddiaz Design
pHyWY|e6b69aa7329347f4bcd431d13b7dc915|2023 Chery Arrizo 8 290T|CC Attribution-NonCommercial-ShareAlike|1|332309|Ddiaz Design
pHBuU|d9ff826c2bf14d81a4fa3c9b6b707376|2023 Chery Omoda 5 290T|CC Attribution-NonCommercial-ShareAlike|1|394838|Ddiaz Design
"""

CC = re.compile(r"^CC(?:\s|0)")


def license_url(label: str) -> str | None:
    kinds = {"Attribution": "by", "Attribution-ShareAlike": "by-sa", "Attribution-NonCommercial": "by-nc",
             "Attribution-NonCommercial-ShareAlike": "by-nc-sa", "Attribution-NoDerivs": "by-nd"}
    k = kinds.get(label.removeprefix("CC ").strip())
    return f"https://creativecommons.org/licenses/{k}/4.0/" if k else None


def status(label: str, downloadable: bool) -> tuple[str, str | None]:
    if not downloadable:
        return "blocked", "автор не разрешил скачивание"
    if not CC.match(label):
        return "blocked", f"лицензия «{label or 'не указана'}» не позволяет хранить и изменять модель"
    if "NoDeriv" in label:
        return "blocked", "лицензия ND запрещает изменения (мы перекрашиваем кузов)"
    return "pending", None


def main() -> None:
    out = []
    for line in RAW.strip().splitlines():
        code, uid, title, label, dl, faces, author = line.split("|")
        st, reason = status(label, dl == "1")
        out.append({"short_code": code, "uid": uid, "title": title, "author": author, "license": label or None,
                    "license_url": license_url(label), "downloadable": dl == "1", "faces": int(faces),
                    "url": f"https://sketchfab.com/3d-models/{uid}", "short_url": f"https://skfb.ly/{code}",
                    "status": st, "blocked_reason": reason})
    path = Path(__file__).resolve().parent.parent / "data" / "catalog" / "sketchfab_models.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for m in out if m["status"] == "pending")
    print(f"{len(out)} моделей: подходят для хранения {ok}, закрыты {len(out) - ok} -> {path.name}")


if __name__ == "__main__":
    main()
