# Helirea ühitaja

Võtab helirea ühelt videolt ja paneb selle teisele versioonile samast videost —
ja näitab enne, kas heli triivib pildist eemale ja kui palju.

Miks lihtne `ffmpeg -map` tavaliselt ei tööta: kaks väljalaset samast filmist on
sageli eri kaadrisagedusega. 25 fps PAL-versioon on 23.976 fps versioonist
**4.1% lühem**, mis teeb **147 sekundit tunnis** — poole filmi peal on heli juba
minuti võrra paigast. `-map` ei paranda seda, sest ta ei muuda kestust.

---

## Kiire algus

```bash
docker compose up
```

Ava <http://localhost:5174>. Videod käivad kausta `media/`, valmis failid
tulevad kausta `work/exports/`.

Kui failid on mujal, muuda `docker-compose.yml`-is volume'i:

```yaml
    volumes:
      - "D:/Filmid:/media:ro"
      - ./work:/work
```

### Natiivne aken

Ehita pakk — **hostis pole Node'i ega npm-i vaja**, konteiner teeb kõik ära:

```bash
docker compose --profile desktop run --rm desktop-build
```

Siis käivita `dist/HelireaUhitaja/Helirea-uhitaja.exe`.

Aken otsib ise projektikausta üles, käivitab konteineri kui see veel ei jookse,
ootab valmis ja avab kasutajaliidese. Kui *tema* konteineri käivitas, peatab ta
selle akna sulgemisel; juba töötavat konteinerit ta ei puutu.

Ehitamine ei vaja Node'i sellepärast, et Electroni äpp *ongi* lihtsalt
`resources/app/` valmis binaari kõrval — ei kompilaatorit, ei pakendajat. Pilt
tõmbab Electroni ametliku arhiivi ja paneb kaks faili sisse.

Teise platvormi jaoks vaheta `docker-compose.yml`-is `ELECTRON_PLATFORM`
(`linux-x64`, `darwin-arm64`).

Electron ise konteineris ei jookse — see vajaks X11/VNC-edastust ja oleks
brauserist halvem. Seega: Docker teeb töö, aken on hostis.

<details>
<summary>Arendus otse allikast (vajab Node 18+)</summary>

```bash
cd desktop && npm install && npm start
```
</details>

---

## Kuidas kasutada

**1. Failid.** `A` on video, mille **pilti hoiame**. `B` on fail, kust **heli
võtame** — võib olla ka pildita helifail (`.mka`, `.ac3`, `.flac`…).
Kaadrisageduste vahe näidatakse kohe ära.

**2. Mõõda nihe.** Tööriist loeb mõlemast failist signaali ja mõõdab ~24 kohas
üle filmi, kui palju heli pildist eemale on. Kaks meetodit:

| Meetod | Mida korreleerib | Millal |
|---|---|---|
| **pilt** | kaadritevaheline liikumisenergia | sama montaaž, ükskõik mis keeled — keelest täiesti sõltumatu |
| **heli** | mitmeribaline spektraalvoog | kui pilt on eri kvaliteediga või B-l pole pilti; toetub muusika/efektide kihile |

Vaikimisi jookseb mõlemad ja valib kindlama.

**3. Graafik on vastus.** Kaks vaadet, nupp „Näita toorest triivi" vahetab:

- **Jääkviga** — mis jääb üle *peale* parandust. Rohelises ribas (±40 ms) =
  märkamatu. Kõik punktid rohelises = lineaarne parandus katab asja täielikult.
- **Toores triiv** — mis juhtuks naiivselt üle tõstes. **Sirge kaldjoon** =
  kaadrisageduse vahe, parandatav. **Trepp või hüpped** = failid on eri lõikega
  (erinev intro, reklaamipausid) ja üks tempoparandus neid kokku ei too.

**4. Parandus.** Mõõdetud väärtused on juba sees. Käsitsi:

- **Nihe** — konstantne viide millisekundites.
- **Tempo** — kiiruse kordaja. Rippmenüüs on tuntud suhted (PAL 25↔23.976,
  NTSC 24↔23.976 jne); kui mõõdetud väärtus ühega neist kattub, ütleb tööriist
  seda ise.
- **Helikõrgus** — `atempo` hoiab kõrguse paigal, `asetrate` muudab kiirust ja
  kõrgust koos. **PAL-paranduseks on `asetrate` õigem**: PAL-i kiirendus tõstis
  omal ajal ka helikõrgust 4%, ja `asetrate` võtab selle tagasi.
- Doonorrea **lohistamine** hiirega muudab nihet; „Mõõda siit" mõõdab praeguses
  vaates ja joondab. Nõrga vaste korral (korrelatsioon alla 0.35) ta *ei*
  joonda, vaid ütleb seda — vale ankur lõhuks hea mudeli ära.

Lainekujud on ülestikku ja alumine on **juba parandatud** — kui löögid on
kohakuti, on asi paigas.

**5. Kuula üle.** Renderdab lühikese lõigu. Vaikimisi „originaal vasakul / uus
paremal" — kõrvaklappidega kuuleb nihet kohe, palju kiiremini kui vaadates.

**6. Ekspordi.** Video kopeeritakse muutmata (`-c:v copy`), ainult heli
kodeeritakse. Originaalheli jääb soovi korral teiseks rajaks. „Vastav ffmpeg
käsk" all on sama asi käsitsi jooksutamiseks.

---

## Kuidas mõõtmine töötab

Mudel on lineaarne: `tB = α·tA + β`, kus `α` on ühtlasi `atempo` kordaja.

1. **Signaal.** Pildist: 12.5 fps-ni hõrendatud 32×18 halltoonkaadrid,
   kaadritevaheline muutus. Helist: 10-ribaline spektraalvoog 50 Hz võrgus.
   Mõlemad vahemällu (`work/cache/`), nii et seadete muutmine on kohene.

2. **Jäme läbimine.** Lühikesed aknad **tugevalt hägustatud** signaalil.
   Hägustamine on hädavajalik: `W`-sekundilise akna otsad triivivad omavahel
   `W·(1−α)` võrra laiali — PAL-i juures 0.32 s 8-sekundilises aknas. Sellest
   kitsamad tunnused ei kohtu üheski nihkes ja korrelatsioonitipp kaob.

3. **Sobitamine RANSAC-iga.** Vähimruutude meetod ei kõlba: korduva mustriga
   pildil annab korrelatsioon vähemuse enesekindlaid vasteid, mis on sekundeid
   mööda, ja need veavad mediaanipõhise sobituse joonelt ära. Konsensus
   leitakse hääletamisega üle kõigi punktipaaride.

4. **Täppisastmed.** B ajastatakse leitud mudeliga ümber ja mõõdetakse uuesti —
   nüüd on jääkvenitus tühine, nii et pikad teravad aknad töötavad ja lahutus
   on millisekundites. Kui täppisaste tuleb tagasi *väiksema* konsensusega kui
   enne, on ta kinni haakunud kõrvaltipu külge ja tulemus visatakse ära.

`confidence` kannab sisemist kooskõla (mitu punkti nõustus, kui laiali need on,
kui suur jääk). Kui see on madal, ütleb tööriist „ei õnnestunud usaldusväärselt
mõõta" — mitte ei anna ilusa välimusega vale vastust.

---

## Kontrollimine

Testklipid, mille triiv on **täpselt teada**:

```bash
python tools/make_test_clips.py media
python tools/selftest.py media/test_A_23.976fps.mkv media/test_B_25fps.mkv 0.959041 -1.52
```

Ootus: mõlemad meetodid tabavad ~20 ms sisse. (Testklippide endi määramatus on
~21 ms — pool kaadrit 23.976 fps juures —, nii et täpsemat sellega mõõta ei saa.)

Ja iga päris eksport üle kontrollida — mõõdab valminud faili kaks helirada
teineteise vastu:

```bash
python tools/verify_export.py work/exports/minu_fail.mkv
```

Peab andma „sünkroonis", nihe alla 40 ms mõlemas otsas.

---

## Kui ei õnnestu

**„Triiv ei ole ühtlane — failid on tõenäoliselt eri lõikega."** Vaata toorest
triivi: kus punktid hüppavad, seal on lõige erinev. Lineaarne parandus ei aita;
praegune tööriist parandab ühe sirge korraga. Töötav lahendus: ekspordi film
tükkide kaupa, iga tüki jaoks oma nihe.

**Mõõtmine ei leia midagi.** Proovi teist meetodit. Kui pildid on eri
väljalasetest (erinev kärbe, logod, taasrestaureeritud), kasuta heli. Kui
helikihid on täiesti erinevad (eri muusika), kasuta pilti. Suurenda „Max
otsing", kui nihe võib olla üle 30 s.

**Heli on paigas, aga kõrgus on nihkes.** Vaheta `atempo` ↔ `asetrate`.

---

## Kataloogid

```
backend/app/
  analysis.py   signaalid, korrelatsioon, RANSAC, iteratiivne täpsustus
  render.py     SyncModel -> ffmpeg filtriahelad, eelvaade, eksport
  main.py       HTTP API
  cache.py      kettavahemälu dekodeeritud signaalidele
  jobs.py       taustatööd
frontend/       ilma ehitussammuta: HTML + ES-moodulid + canvas
tools/          testklippide generaator, enesetest, ekspordi kontroll
desktop/        Electroni kest + Dockerfile, mis paki kokku paneb
dist/           ehitatud töölauapakk (ei lähe versioonihaldusesse)
```

Lokaalne arendus ilma Dockerita: `./dev.sh` (vajab `.venv`-i ja ffmpeg'i PATH-is
või `FFMPEG_BIN`/`FFPROBE_BIN` keskkonnamuutujates).
