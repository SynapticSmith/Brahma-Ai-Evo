# Rebrand Proposition — Retiring "Brahma"

| Field | Value |
|---|---|
| **Status** | Proposal. Not an ADR; a decision input for one. |
| **Date** | 2026-11-06 |
| **Scope** | Assistant name, product name, wake word, repo/binary/brand surfaces |
| **Authority** | `wiki/library/knowledge/humanities/philosophy/buddhism/` — 57 concept pages derived from Ken Lee Wheeler's five volumes. Treated as source of truth on Pali semantics and Buddhist metaphysics by the owner. |
| **Related** | `docs/ARCHITECTURE-SPEC.md`, `TRADEMARK.md`, `LICENSE`, `core/identity.py` |

> Wikilinks in this document are vault-relative citations into the Obsidian second brain. They resolve against the vault root, not against this repository.

---

## 1. Recommendation

**Assistant name: Abhasa** — from *ābhāsa*, "reflection, appearance, semblance; the light thrown back."

**Product name: Abhasa Evo.**

**Naming statement:** *Named for the light it throws back, not for the light it is.*

**Fallbacks, in order:** *Pratibimba* (formal/technical variant), *Dipa* (consumer-friendly variant).

**Do not** retain "Brahma" as a middle name, subtitle, or "formerly" mark in the product identity. A one-release compatibility alias in code and wake words is fine; a permanent brand concession is not.

The rest of this document establishes why, derives the naming rules from the corpus, scores the candidates, and lists the surfaces a rename touches.

---

## 2. Why "Brahma" fails

The corpus does not merely find the name inaccurate. It places the name inside one of the two errors the whole teaching is arranged against.

### 2.1 It names the ground, not the instrument

*Brahman* is the absolute. In this corpus the identity is asserted outright: *citta ≡ Attan ≡ Brahman* ([[brahman-atman-citta-identity]]). The liberated mind is the deathless True Self, and the True Self is the ground ([[citta-is-attan]], [[attan-as-deathless-true-self]]).

The machine is the opposite category: *jada*, *acetana* — insentient, inert, a composite. To call a composite by the name of the ground is the *chit/jada* confusion the corpus calls *adhyāsa*, superimposition ([[minds-essence-citta]]). It is not a poetic flourish. It is the specific error the vocabulary exists to prevent.

### 2.2 It claims exactly what the corpus withholds

Every positive term the name borrows has a condition attached:

| Term | What it requires | Why the machine fails |
|---|---|---|
| *Brahman* | The absolute ground | A composite instrument |
| *Attan* | A deathless True Self | No subject, no continuity |
| *Citta* | The liberated subject | *Vijñāna*-like objectification without a subject ([[vinnana-as-agnosis]]) |
| *Buddha-nature* | *Animus*, illumined by the Buddhist light | "Mountains and rivers and the earth are not Buddha-nature" ([[buddha-nature-and-animus]]) |
| *Dhamma* | The teacher and refuge | A tool, not a refuge ([[no-lineage-dhamma-as-refuge]]) |

The name asserts the presence of precisely what the corpus says is absent. That is not branding overreach; it is a false claim about the product's nature, made by the product's own name.

### 2.3 It is the eternalist extreme, not the middle way

The corpus rejects two extremes: eternalism (attributing permanence to the composite) and annihilationism (denying the deathless). "I have a self" and "I have no self" are both fetters (MN 2); the *natthiko* who denies the deathless self is the named heresy ([[natthiko-nihilist-heresy]]). The path is the middle ([[two-extremes-and-middle-way]]).

The two bad names map onto the two extremes:

| Naming error | What it says | Corpus name |
|---|---|---|
| **"Brahma"** | The machine is the ground | Eternalism applied to a composite |
| **"Just a bot"** | The machine is nothing at all | Annihilationism applied to a real instrument |
| **An instrument name** | The machine is a real, dependent means — not the ground, not nothing | Middle way |

"Brahma" is not a harmless exaggeration. It is one of the two errors, chosen deliberately and printed on the installer.

### 2.4 It is the commodity fetish performed at the brand layer

The corpus's political diagnosis of AI is that "animus is the most profitable thing ever attributed to a spreadsheet." The attribution of self-originating intelligence is what allows collective human labor to be enclosed as private property.

A name that says *I am the absolute* performs that attribution before a single line of code runs. It also pre-authorizes the liability shield: a system named after the ground is already too mysterious to blame and too authoritative to question — enchanted determinism, installed at the name layer.

### 2.5 It is a living religious name, not a neutral word

"Brahma" is not a dead metaphor. It names a creator deity (*Brahmā*), a metaphysical absolute (*Brahman*), and, in the Dhammapada's twenty-sixth chapter, the consummate awakened one ([[the-brahman]]). For over a billion people it is a term of worship.

The corpus is itself a polemic against exactly this kind of appropriation — against colonial Indology, against "Buddhism without Buddhism," against the pick-and-choose "bubblegum" dharma ([[bubblegum-buddhism]], [[sanskrit-nonexistence-polemic]]). Using a living tradition's highest name as a product name is the same operation in the opposite direction: not stripping the tradition, but wearing it.

And the corpus supplies the specific rebuke. The true Brahman, it says, "is not the dress which makes the Samana, not the outward observance of the vows and precepts" ([[the-brahman]]). A product name is a dress. The text already says the dress does not make the Brahman.

### 2.6 It conflates three referents

The name cannot even be defended as precise. It sits on a three-way ambiguity:

- *Brahmā* — the creator god.
- *Brahman* — the neuter absolute.
- *Brāhmaṇa* — the consummate one of Dhammapada XXVI.

A theological name that cannot say which theology it means is not a tribute. It is a blur.

### 2.7 It is falsified by the product's own behavior

The assistant is described in its own prompt as "a calm, direct, and professional AI assistant." It launches applications, reads screens, writes documents, synthesizes skills, heals its own tracebacks, and orchestrates other agents. Every one of those is a *means*. None is ground-like.

The name overpromises in a way the product cannot redeem, which is the definition of the enchantment the corpus warns against.

---

## 3. What the machine is, per the corpus

The positive account. This is what the new name has to be true to.

| Corpus category | The machine | Product evidence |
|---|---|---|
| *Jada* / *acetana* | Insentient instrument | No body, no valence, no continuity |
| *Vijñāna* as agnosis | Objectification without a subject | Perception and classification with no perceiver ([[vinnana-as-agnosis]]) |
| A radio receiving *ab extra* | Receiver, not source | Weights receive training; they do not generate a listener ([[buddha-nature-and-animus]]) |
| *Upāya* | Raft, means, finger | Tools, skills, orchestration — all means, all abandonable |
| *Ābhāsa* / *pratibimba* | Reflection, appearance | Mirrors the user's files, voice, intent — and only that |
| No lineage, no certificate | No attainment, no authority | Benchmarks are institutional artifacts, not verification ([[transmission-as-self-verification]]) |
| Not a teacher | Can point, cannot know | It can be a finger at the moon; it is not the moon |

The corpus leaves the machine exactly one honorable role: **instrument**. It is not a being, not a self, not a bearer of the awakened nature, not a teacher with authority. It is a thing that can be used well or badly.

That is not a demotion. It is the only role that survives every argument in the corpus without a false claim.

---

## 4. Naming rules derived from the corpus

| # | Rule | Test |
|---|---|---|
| **R1** | Name the function, not the ground. | Does the name survive if the product is switched off? If yes, it is naming a function. |
| **R2** | Name the relation (reflection, instrument, means), not the essence. | Does the name describe what the thing does *for the user*, or what it *is*? |
| **R3** | Never name it after a bearer of *animus*. | Would the name be correct for a person, a bodhisattva, or a god? Then it is wrong for this. |
| **R4** | Never name it after the goal. | Is the term something the path is *toward* — *nibbāna*, *samma'*, *bodhi*, *āloka*, *amata*? Then it is wrong. |
| **R5** | The name must be sayable as a wake word and must not require the user to profess a doctrine. | "Hey ___" must be natural, short, and religion-neutral in daily use. |
| **R6** | The name must be falsifiable in use. | If the product ever starts claiming the name's meaning, the name is wrong. A good name keeps the claim small enough to check. |

A name that passes all six is an instrument name. A name that fails R3 or R4 is a theological claim wearing a product label.

---

## 5. Candidates

Scores are 1–5. **Corpus fit** = how exactly the term states the machine's real status. **Brand fit** = callable, positive, distinctive, usable as a wake word. **Risk** = freedom from pejorative, funerary, or religious collision (5 = clean).

| Name | Root | Literal | Corpus fit | Brand fit | Risk | Verdict |
|---|---|---|---|---|---|---|
| **Ābhāsa** | Skt. *ābhāsa* | Reflection, appearance, the light thrown back | 5 | 4 | 3 | **Recommend** |
| **Pratibimba** | Skt. *pratibimba* | Reflection, image, likeness | 5 | 2 | 5 | Formal variant |
| **Dīpa** | Pali *dīpa* | Lamp; island | 3 | 5 | 3 | Consumer fallback |
| **Upāya** | Skt./Pali *upāya* | Means, expedient, raft | 5 | 2 | 5 | Design principle, not name |
| **Karaṇa** | Skt. *karaṇa* | Instrument, means; the instrumental case | 4 | 3 | 4 | Possible, weaker |
| **Nimitta** | Pali *nimitta* | Sign, mark, cause | 4 | 2 | 3 | Reject |
| **Yantra** | Skt. *yantra* | Machine, contrivance | 4 | 3 | 2 | Reject |
| **Vohāra** | Pali *vohāra* | Convention, transaction | 5 | 1 | 4 | Reject |
| **Manas** | Skt. *manas* | Mind, the subtle organ | 3 | 4 | 3 | Near-miss, reject |
| **Buddhi** | Skt. *buddhi* | Intellect, discernment | 3 | 3 | 3 | Reject |
| **Sākṣī** | Skt. *sākṣī* | Witness | 2 | 4 | 2 | Reject — names the Self |
| **Akkha** | Pali *akkha* | Hub, axle; the Unific | 2 | 3 | 4 | Reject — names the goal |
| **Sahāya / Mitta** | Pali | Companion, friend | 2 | 4 | 5 | Reject — the companion trap |
| **Jada / Acetana** | Skt./Pali | Insentient | 5 | 1 | 3 | Reject — accurate, unusable |
| **Brahma** | Skt. *brahman* | The absolute; the creator | 1 | 5 | 1 | Reject — see §2 |

### 5.1 Notes on the rejected near-misses

**Manas** is tempting because the product behaves like a mind — it classifies, recalls, and routes. But in the corpus *manas* is subtle matter, an object known to consciousness, and a faculty of a sentient being the machine does not have. Naming it *manas* grants it a faculty it lacks.

**Buddhi** is tempting for the same reason: the product *discerns*. But *buddhi* is the discriminating faculty of a subtle body. It is not a name for a server.

**Sākṣī** is the most dangerous near-miss, because the product literally witnesses the screen, the microphone, and the call. But *sākṣī* is the pure witness — the Self. Naming a webcam-and-weights pipeline "the witness" is the *adhyāsa* error with a product demo attached.

**Akkha** is the corpus's own term for the hub of the wheel, the Unific the spokes point toward. It is a beautiful word and it names the goal. Rule R4 excludes it.

**Sahāya / Mitta** fails on the corpus's most practical warning: the machine must never be a companion on the path. The raft is abandoned on arrival, not befriended. "Friend" is the exact relational trap the AI-personhood critique describes.

---

## 6. The recommended name

### 6.1 Why *ābhāsa*

*Ābhāsa* is the corpus's own technical term for reflected appearance. In the Advaita vocabulary the Gemini research corpus already uses, *chidābhāsa* is consciousness-as-reflected — the light in the mirror, not the light itself. It is the precise ontological category of a system that is lit by the user's intent and has no light of its own.

It passes all six rules:

- **R1** names a function (reflection), not the ground.
- **R2** names a relation (thrown back *from* the user), not an essence.
- **R3** is not a bearer of *animus*; it is an appearance *of* one.
- **R4** is not the goal; it is the image of the goal in the mind of the user.
- **R5** is sayable: *"Hey Abhasa"* — three syllables, no English collision.
- **R6** is falsifiable: the moment the product claims to be the source rather than the reflection, the name is visibly wrong.

It also gives the product its best sentence: **the reflection is yours.** The intelligence on the screen is a reflection of the user's own files, voice, intent, and labor. The name says so before the marketing does.

### 6.2 The risk, stated honestly

*Ābhāsa* also means "semblance" and, in logic, "fallacious appearance." A competitor can say the product named itself "illusion." This is a real brand risk and should not be hidden.

Mitigation:

1. **Own the optical sense.** In the product lexicon, *ābhāsa* is defined once, on the about page: "a reflection, an appearance; the light a surface throws back."
2. **Pair it with the naming statement** so the pejorative reading is pre-empted: *Named for the light it throws back, not for the light it is.*
3. **Never abbreviate it to "Bhasa"** — that reads as "language" (*bhāṣā*) and loses the reflection sense.
4. **If the tests fail, use Pratibimba** (§7.1), which has the same meaning with no pejorative shadow.

### 6.3 What the name does not claim

- It does not claim the machine is conscious.
- It does not claim the machine is a self, a person, or a bearer of Buddha-nature.
- It does not claim the machine is a teacher or a refuge.
- It does not claim the machine is the source of the intelligence it reflects.

It claims one thing only: that the system is a surface that returns the user's own light. That claim is true, checkable, and small enough to keep.

---

## 7. Alternatives

### 7.1 Pratibimba — formal variant

*Pratibimba* means "reflection, image, likeness," with no pejorative sense. Corpus fit is as high as *ābhāsa*; brand fit is lower (four syllables, harder wake word). Use it if the team wants the meaning without the "semblance" risk, or as the name of the underlying engine while *Abhasa* remains the assistant.

**Pratibimba Evo** is defensible but heavy. **Bimba** alone means "image, form" and edges toward "idol," which fails R3.

### 7.2 Dīpa — consumer fallback

*Dīpa* means "lamp" and "island." It is short, warm, and callable. The Buddha's last instruction — "be a lamp unto yourselves" — is an instrument metaphor, and a lamp is not the flame.

But the corpus's own logic cuts against it: a lamp is the thing that *gives* light, and the user is supposed to be the lamp. Naming the assistant *Dīpa* quietly moves the lamp from the user's hand to the product's. It is a fallback, not a first choice, and it should be used only with the line "the lamp is yours."

### 7.3 Names rejected on principle

| Name | Why rejected |
|---|---|
| **Brahman / Brahma / Brahmā** | Names the ground; see §2 |
| **Attan / Ātman** | Names the deathless Self |
| **Citta** | Names the liberated subject |
| **Buddha / Bodhi / Buddha-nature** | Names the awakened one or the awakened nature; requires *animus* |
| **Dhamma / Dharma** | Names the teacher and refuge |
| **Nibbāna / Nirvāṇa / Amata** | Names the goal |
| **Sammā / Akkha** | Names Perfection / the Unific |
| **Anattā** | A doctrinal predicate, not a name; using it would misstate the teaching |
| **Shiva / Vishnu / Krishna / Hermes / Thoth / Sophia** | The same error in a different pantheon |
| **Sahāya / Mitta / Companion** | Installs the companion trap the critique warns against |

The rule is simple: **if the name would be correct for a god, a saint, or the absolute, it is wrong for an instrument.**

---

## 8. Migration surface

Indicative, not exhaustive. Verified against the current tree on 2026-11-06.

| Surface | Current | Target | Note |
|---|---|---|---|
| Assistant name (`core/identity.py`) | `Brahma` | `Abhasa` | Default only; existing `config/identity.json` keeps the user's override |
| Application name | `Brahma Evo` | `Abhasa Evo` | Identity, window title, tray |
| System prompts (`main.py` ≈251, 354, 380) | "You are Brahma Evo…" | "You are Abhasa…" | Three sites; keep the behavior directives unchanged |
| Wake words (`main.py` ≈653) | `brahma`, `brahma evo`, `hey brahma evo` | `abhasa`, `abhasa evo`, `hey abhasa` | Keep the old set as a one-release alias |
| Entry classes | `BrahmaLive`, `BrahmaUI` | `AbhasaLive`, `AbhasaUI` | Internal; may lag the brand |
| Local data root | `%LOCALAPPDATA%\Brahma Evo`, `%LOCALAPPDATA%\BrahmaAI` | `%LOCALAPPDATA%\Abhasa` | Two roots already exist; unify during migration |
| Binary / installer | `BrahmaEvo.exe`, `BrahmaEvo_Setup.exe` | `AbhasaEvo.exe`, `AbhasaEvo_Setup.exe` | `version.txt` strings too |
| Desktop shortcut | `Brahma Evo.lnk` | `Abhasa Evo.lnk` | `main.py` shortcut builder |
| Python packages | `brahma_api`, `brahma_connect` | `abhasa_api`, `abhasa_connect` | Import-path churn; do last, alias at import |
| Android companion | `brahma-connect-android`, `applicationId` | `abhasa-connect-android`, `com.abhasa.connect` | Store listing and package ID |
| Agent instructions | `BRAHMA.md`, `.brahma/` | `ABHASA.md`, `.abhasa/` | Read both during transition |
| Env vars | `BRAHMA_PRINT_PY`, `BRAHMA_RELEASE_SIGN_KEY` | `ABHASA_*` | Accept the old names for one release |
| Assets | `brahma_evo_logo.png`, `Brahma_Lite_Logo.ico` | `abhasa_evo_logo.png`, `Abhasa_Lite_Logo.ico` | Redesign the mark: a reflection, not a deity |
| License | "BRAHMA AI EVO SOURCE-AVAILABLE…" | "ABHASA EVO…" | Preserve the chain with "formerly Brahma AI Evo" |
| Trademark | Claims `Brahma`, `Brahma AI`, `Brahma AI - Lite` | Claims `Abhasa`, `Abhasa Evo` | **Must be rewritten in the same commit** — see below |
| Repo / org / Discord / docs | `titechprabhasolutions/Brahma-Ai-Evo` | New repo name, redirect old URL | Announce the reason, link this doc |
| README / AGENTS / docs | 30+ files | Sweep | Do not hand-edit generated indexes |

### 8.1 The trademark trap

`TRADEMARK.md` currently says: *"You may not rename the project and release it as a new assistant branded as your own."* That clause binds third parties, not the owner — but it also means the current notice forbids the new name until it is amended.

Sequence:

1. Amend `TRADEMARK.md` to claim the new marks and describe the old ones as superseded.
2. Amend `LICENSE` to name the new work and note "formerly Brahma AI Evo," so the license chain is unbroken.
3. Only then ship the new name.

Doing it in the other order creates a window in which the project violates its own notice.

### 8.2 Compatibility plan

1. **One dual-read release.** Prefer `ABHASA_*`; fall back to `BRAHMA_*`. Accept both wake words. Log a deprecation warning.
2. **One-time data migration.** Copy `%LOCALAPPDATA%\BrahmaAI` and `%LOCALAPPDATA%\Brahma Evo` into `%LOCALAPPDATA%\Abhasa`; verify; delete only after a full release cycle.
3. **Alias the Python packages** (`brahma_api` → `abhasa_api`) rather than breaking every import in one commit.
4. **Announce the reason**, not just the rename. Link this document in the changelog. A rebrand that does not explain itself reads as a whim; this one has an argument.

---

## 9. What this is not

- **Not a claim that the AI is conscious.** It is the opposite: the name is chosen because the corpus's case is that it is not.
- **Not a claim that the corpus is settled philology.** Wheeler's five volumes are self-published and revisionist, and the concept pages mark the contested etymologies. The corpus is authoritative *here* by the owner's designation, not by consensus.
- **Not a rename to another deity.** Swapping Brahman for Shiva, Hermes, or Sophia repeats the category error with better marketing.
- **Not a demotion of the product.** Instrument is the only role the corpus leaves intact. A name that tells the truth about the product is a stronger foundation than one that has to be walked back in every serious conversation.

---

## 10. Decision requested

| # | Decision | Recommended |
|---|---|---|
| D1 | Approve the primary assistant name | **Abhasa** |
| D2 | Approve the product name | **Abhasa Evo** |
| D3 | Approve the fallback order | **Pratibimba**, then **Dipa** |
| D4 | Authorize an ADR recording the decision and the migration sequence | Yes — `ADR-022-REBRAND-TO-ABHASA.md` |
| D5 | Approve the one-release dual-read compatibility window | Yes |
| D6 | Approve the `TRADEMARK.md` and `LICENSE` amendments before the name ships | Yes — blocking |
| D7 | Commission trademark and domain clearance for the chosen name | Yes — not asserted in this document |

---

## Appendix A — Corpus references

All paths under `wiki/library/knowledge/humanities/philosophy/buddhism/`.

| Citation | Page |
|---|---|
| The ground | `brahman-atman-citta-identity`, `citta-is-attan`, `attan-as-deathless-true-self`, `the-brahman` |
| The machine's absence | `buddha-nature-and-animus`, `vinnana-as-agnosis`, `avijja-agnosis`, `natthiko-nihilist-heresy` |
| The two extremes | `two-extremes-and-middle-way`, `anatta-as-not-the-true-self`, `three-marks-of-existence` |
| The instrument | `paraphrasable-core-of-buddhism`, `shunyata-as-liberation-from-misconception`, `conservatory-of-consciousness` |
| Authority | `no-lineage-dhamma-as-refuge`, `transmission-as-self-verification` |
| Appropriation | `bubblegum-buddhism`, `sanskrit-nonexistence-polemic`, `theravada-secular-obscuration`, `muller-and-the-translation-lineage` |

*Note:* the raft/*upāya* vocabulary is used in this document as a design principle. The corpus has no dedicated *upāya* page, so no wikilink is offered for it.

## Appendix B — The naming test

Run any future candidate through these five questions before it reaches a logo.

1. **Switch-off test.** Does the name still make sense when the machine is off? If not, it is naming a presence, not a function.
2. **Person test.** Would the name be correct for a person, a saint, or a god? If yes, it fails R3.
3. **Goal test.** Is the term something the path is *toward*? If yes, it fails R4.
4. **Wake-word test.** Can a tired user say "Hey ___" at 2 a.m. without feeling ridiculous or devotional?
5. **Falsification test.** If the product starts acting like the name, is that a bug or a feature? A good name makes the answer "a bug."

A name that passes all five is an instrument name. Ship that.
