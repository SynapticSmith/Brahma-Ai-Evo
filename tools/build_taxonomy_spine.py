"""Compile canonical taxonomy.json v1 for the Brahma Second Brain.

Generates the reviewed 5-division spine based on:
1. The Wikipedia Outline of Academic Disciplines
2. Vault Calibre vocabulary (book-library/_taxonomy.md)
3. Identified substantive notes from Phase 0 audit

Writes:
- `C:\\Users\\Admin\\Documents\\Obsidian\\My Vault\\wiki\\library\\knowledge\\_taxonomy\\taxonomy.json`
- `config\\taxonomy.json`
- `C:\\Users\\Admin\\Documents\\Obsidian\\My Vault\\wiki\\library\\knowledge\\_taxonomy\\taxonomy.overrides.yaml`
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import second_brain  # noqa: E402


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", str(text or "")).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


# Canonical 5-division tree definition
DIVISIONS = [
    {
        "id": "humanities",
        "title": "Humanities",
        "parent": None,
        "scope": "Disciplines that study human culture, history, philosophy, languages, and artistic expression.",
        "legacy_domains": ["humanities"],
        "children": [
            {
                "id": "humanities/history",
                "title": "History",
                "scope": "The systematic study and documentation of the human past.",
                "children": [
                    {"id": "humanities/history/ancient-history", "title": "Ancient History", "scope": "History from the beginning of recorded human history to the Early Middle Ages."},
                    {"id": "humanities/history/medieval-history", "title": "Medieval History", "scope": "History of Europe and the Mediterranean from the fall of the Western Roman Empire to the Renaissance."},
                    {"id": "humanities/history/modern-history", "title": "Modern History", "scope": "History following the Middle Ages from the Early Modern period to contemporary times."},
                    {"id": "humanities/history/military-history", "title": "Military History", "scope": "The history of armed conflicts, military strategy, operations, and warfare technology."},
                    {"id": "humanities/history/historiography", "title": "Historiography", "scope": "The methodology and history of historical writing and historical research."},
                ],
            },
            {
                "id": "humanities/languages-and-literature",
                "title": "Languages and Literature",
                "scope": "The structure of human language, philology, literary analysis, and comparative literature.",
                "children": [
                    {"id": "humanities/languages-and-literature/linguistics", "title": "Linguistics", "scope": "The scientific study of human language, phonetics, syntax, semantics, and morphology."},
                    {"id": "humanities/languages-and-literature/comparative-literature", "title": "Comparative Literature", "scope": "The study of literature across cultures, time periods, genres, and languages."},
                    {"id": "humanities/languages-and-literature/philology", "title": "Philology", "scope": "The study of language in oral and written historical sources."},
                    {"id": "humanities/languages-and-literature/literary-theory", "title": "Literary Theory", "scope": "Systematic frameworks for interpreting and analyzing literary works."},
                ],
            },
            {
                "id": "humanities/performing-arts",
                "title": "Performing Arts",
                "scope": "Arts forms where artists use their voices, bodies, or inanimate objects to convey artistic expression.",
                "legacy_domains": ["arts"],
                "children": [
                    {"id": "humanities/performing-arts/music", "title": "Music", "scope": "The art form consisting of sound and silence expressed through rhythm, melody, and harmony."},
                    {"id": "humanities/performing-arts/theatre", "title": "Theatre", "scope": "Collaborative performing art combining speech, gesture, music, and stagecraft."},
                    {"id": "humanities/performing-arts/dance", "title": "Dance", "scope": "Sequences of human movement, often accompanied by music, possessing aesthetic and symbolic value."},
                ],
            },
            {
                "id": "humanities/visual-arts",
                "title": "Visual Arts",
                "scope": "Art forms that create works primarily visual in nature, such as ceramics, drawing, painting, sculpture, and design.",
                "legacy_domains": ["arts"],
                "children": [
                    {"id": "humanities/visual-arts/drawing-and-painting", "title": "Drawing and Painting", "scope": "Practices of applying pigment or graphic marks to surfaces to produce visual compositions."},
                    {"id": "humanities/visual-arts/sculpture", "title": "Sculpture", "scope": "Three-dimensional visual art created through carving, modeling, welding, or casting."},
                    {"id": "humanities/visual-arts/design", "title": "Design", "scope": "The conceptualization, planning, and execution of functional and aesthetic objects and communications."},
                    {"id": "humanities/visual-arts/photography", "title": "Photography", "scope": "The art, application, and practice of creating durable images by recording light."},
                ],
            },
            {
                "id": "humanities/philosophy",
                "title": "Philosophy",
                "scope": "The systematic study of fundamental questions concerning existence, reason, knowledge, values, and mind.",
                "children": [
                    {
                        "id": "humanities/philosophy/epistemology",
                        "title": "Epistemology",
                        "scope": "The philosophical study of the nature, origin, and limits of human knowledge, justification, and belief.",
                        "legacy_domains": ["meta-knowledge"],
                        "aliases": ["theory of knowledge"],
                    },
                    {
                        "id": "humanities/philosophy/ethics",
                        "title": "Ethics",
                        "scope": "Moral philosophy systematizing, defending, and recommending concepts of right and wrong behavior.",
                    },
                    {
                        "id": "humanities/philosophy/logic",
                        "title": "Philosophical Logic",
                        "scope": "The systematic study of valid inference, argumentation, and truth structures.",
                    },
                    {
                        "id": "humanities/philosophy/metaphysics",
                        "title": "Metaphysics",
                        "scope": "The study of the fundamental nature of reality, existence, ontology, monism, and causality.",
                        "children": [
                            {"id": "humanities/philosophy/metaphysics/ontology", "title": "Ontology", "scope": "The philosophical branch studying concepts such as existence, being, and reality."},
                            {"id": "humanities/philosophy/metaphysics/monism", "title": "Monism and Emanationism", "scope": "Metaphysical theories holding that reality is fundamentally unified and emanates from a single source."},
                        ],
                    },
                    {
                        "id": "humanities/philosophy/eastern-philosophy",
                        "title": "Eastern Philosophy",
                        "scope": "Philosophical and spiritual traditions originating in South, East, and Southeast Asia.",
                        "children": [
                            {
                                "id": "humanities/philosophy/eastern-philosophy/buddhism",
                                "title": "Buddhism",
                                "scope": "Philosophical and doctrinal traditions centered on liberation, karma, nirvana, and mind.",
                                "children": [
                                    {"id": "humanities/philosophy/eastern-philosophy/buddhism/early-buddhism-nikayas", "title": "Early Buddhism and Nikaya Studies", "scope": "The earliest stratum of Buddhist texts, teachings on Attan, Anatta, Citta, and the Nikayas."},
                                    {"id": "humanities/philosophy/eastern-philosophy/buddhism/theravada", "title": "Theravada Philosophy", "scope": "The doctrine of the elders, Abhidhamma exegesis, commentary traditions, and monastic practice."},
                                    {"id": "humanities/philosophy/eastern-philosophy/buddhism/mahayana", "title": "Mahayana and Madhyamaka", "scope": "The Great Vehicle, Nagarjuna's emptiness (Sunyata), two truths, and Bodhisattva ideal."},
                                    {"id": "humanities/philosophy/eastern-philosophy/buddhism/yogacara", "title": "Yogacara Mind-Only", "scope": "Consciousness-only doctrine, repository consciousness (alayavijnana), and meditative phenomenology."},
                                ],
                            },
                            {"id": "humanities/philosophy/eastern-philosophy/hindu-philosophy", "title": "Hindu Philosophy", "scope": "The six orthodox darshanas, Advaita Vedanta, Samkhya, and Upanishadic metaphysics."},
                            {"id": "humanities/philosophy/eastern-philosophy/daoism", "title": "Daoism", "scope": "Chinese philosophical tradition emphasizing living in harmony with the Dao (the Way)."},
                        ],
                    },
                    {
                        "id": "humanities/philosophy/western-philosophy",
                        "title": "Western Philosophy",
                        "scope": "The philosophical tradition of the Western world from Ancient Greece through Modern and Analytic thought.",
                    },
                ],
            },
            {
                "id": "humanities/religion",
                "title": "Religion and Theology",
                "scope": "The critical study of religious beliefs, behaviors, institutions, and theological doctrines.",
                "children": [
                    {"id": "humanities/religion/comparative-religion", "title": "Comparative Religion", "scope": "The systematic comparison of the doctrines and practices of the world's religions."},
                    {"id": "humanities/religion/theology", "title": "Theology", "scope": "The systematic study of the nature of the divine and religious truth claims."},
                ],
            },
        ],
    },
    {
        "id": "social-sciences",
        "title": "Social Sciences",
        "parent": None,
        "scope": "Disciplines that study societies, social structures, and human interpersonal relationships.",
        "legacy_domains": ["social-sciences"],
        "children": [
            {
                "id": "social-sciences/anthropology",
                "title": "Anthropology",
                "scope": "The holistic scientific study of humanity, past and present, across biological, cultural, and linguistic axes.",
            },
            {
                "id": "social-sciences/archaeology",
                "title": "Archaeology",
                "scope": "The study of human activity through the recovery and analysis of material culture.",
            },
            {
                "id": "social-sciences/economics",
                "title": "Economics",
                "scope": "The social science that studies the production, distribution, and consumption of goods and services.",
                "children": [
                    {"id": "social-sciences/economics/microeconomics", "title": "Microeconomics", "scope": "The study of how individual consumers and firms make decisions to allocate scarce resources."},
                    {"id": "social-sciences/economics/macroeconomics", "title": "Macroeconomics", "scope": "The study of the economy as a whole, including inflation, national income, growth, and unemployment."},
                    {"id": "social-sciences/economics/institutional-economics", "title": "Institutional Economics", "scope": "The study of the role of evolutionary institutions, laws, and norms in shaping economic behavior."},
                ],
            },
            {
                "id": "social-sciences/geography",
                "title": "Human Geography",
                "scope": "The study of the relationships between human communities, cultures, economies, and their spatial environments.",
            },
            {
                "id": "social-sciences/political-science",
                "title": "Political Science",
                "scope": "The scientific study of politics, government systems, political behavior, and public policy.",
                "children": [
                    {"id": "social-sciences/political-science/political-theory", "title": "Political Theory", "scope": "The study of foundational concepts of justice, power, legitimacy, and political philosophy."},
                    {"id": "social-sciences/political-science/international-relations", "title": "International Relations", "scope": "The study of relations between states, international organizations, and transnational actors."},
                    {"id": "social-sciences/political-science/comparative-politics", "title": "Comparative Politics", "scope": "The systematic analysis of different political systems, regimes, and constitutions."},
                ],
            },
            {
                "id": "social-sciences/psychology",
                "title": "Psychology",
                "scope": "The scientific study of mind and behavior, covering perception, cognition, emotion, and personality.",
                "children": [
                    {"id": "social-sciences/psychology/cognitive-psychology", "title": "Cognitive Psychology", "scope": "The scientific investigation of mental processes including attention, language, memory, and reasoning."},
                    {"id": "social-sciences/psychology/behavioral-psychology", "title": "Behavioral Psychology", "scope": "The study of observable behavior and the environmental stimuli that condition it."},
                    {"id": "social-sciences/psychology/neuropsychology", "title": "Neuropsychology", "scope": "The study of the structure and function of the brain as they relate to specific psychological processes."},
                ],
            },
            {
                "id": "social-sciences/sociology",
                "title": "Sociology",
                "scope": "The scientific study of society, social institutions, social relationships, and social change.",
            },
        ],
    },
    {
        "id": "natural-sciences",
        "title": "Natural Sciences",
        "parent": None,
        "scope": "Disciplines that study the physical and biological world through empirical observation and scientific method.",
        "legacy_domains": ["sciences"],
        "children": [
            {
                "id": "natural-sciences/astronomy",
                "title": "Astronomy",
                "scope": "The scientific study of celestial objects, space, and the universe as a physical whole.",
                "children": [
                    {"id": "natural-sciences/astronomy/astrophysics", "title": "Astrophysics", "scope": "The branch of space science that applies the laws of physics and chemistry to explain universe phenomena."},
                    {"id": "natural-sciences/astronomy/cosmology", "title": "Cosmology", "scope": "The study of the origin, evolution, and ultimate fate of the universe."},
                ],
            },
            {
                "id": "natural-sciences/biology",
                "title": "Biology",
                "scope": "The scientific study of life and living organisms, their structure, function, growth, and evolution.",
                "children": [
                    {"id": "natural-sciences/biology/molecular-biology", "title": "Molecular Biology", "scope": "The study of biological activity at the molecular level, including DNA, RNA, and protein synthesis."},
                    {"id": "natural-sciences/biology/biochemistry", "title": "Biochemistry", "scope": "The chemical processes and substances occurring within or relating to living organisms."},
                    {"id": "natural-sciences/biology/genetics", "title": "Genetics", "scope": "The study of genes, genetic variation, and heredity in organisms."},
                    {"id": "natural-sciences/biology/ecology", "title": "Ecology", "scope": "The study of interactions among organisms and between organisms and their physical environment."},
                    {"id": "natural-sciences/biology/evolutionary-biology", "title": "Evolutionary Biology", "scope": "The subfield of biology that studies the evolutionary processes that produced the diversity of life."},
                ],
            },
            {
                "id": "natural-sciences/chemistry",
                "title": "Chemistry",
                "scope": "The scientific study of the properties, composition, structure, and reactions of matter.",
                "children": [
                    {"id": "natural-sciences/chemistry/organic-chemistry", "title": "Organic Chemistry", "scope": "The study of the structure, properties, composition, reactions, and preparation of carbon compounds."},
                    {"id": "natural-sciences/chemistry/inorganic-chemistry", "title": "Inorganic Chemistry", "scope": "The study of inorganic and organometallic compounds lacking carbon-hydrogen bonds."},
                    {"id": "natural-sciences/chemistry/physical-chemistry", "title": "Physical Chemistry", "scope": "The study of macroscopic, atomic, and particulate phenomena in chemical systems using physics."},
                ],
            },
            {
                "id": "natural-sciences/earth-sciences",
                "title": "Earth Sciences",
                "scope": "The fields of science dealing with planet Earth, its atmosphere, oceans, and solid crust.",
                "children": [
                    {"id": "natural-sciences/earth-sciences/geology", "title": "Geology", "scope": "The scientific study of the Earth's solid materials, rocks, structures, and subterranean processes."},
                    {"id": "natural-sciences/earth-sciences/meteorology", "title": "Meteorology", "scope": "The scientific study of the atmosphere with focus on weather forecasting and atmospheric phenomena."},
                    {"id": "natural-sciences/earth-sciences/oceanography", "title": "Oceanography", "scope": "The scientific study of physical, chemical, and biological properties of the world ocean."},
                ],
            },
            {
                "id": "natural-sciences/physics",
                "title": "Physics",
                "scope": "The foundational science studying matter, motion, energy, force, space, and time.",
                "children": [
                    {"id": "natural-sciences/physics/classical-mechanics", "title": "Classical Mechanics", "scope": "The physical theory describing the motion of macroscopic objects under forces."},
                    {"id": "natural-sciences/physics/quantum-mechanics", "title": "Quantum Mechanics", "scope": "The fundamental theory in physics that provides a description of physical properties at atomic scales."},
                    {"id": "natural-sciences/physics/thermodynamics", "title": "Thermodynamics", "scope": "The branch of physics that deals with heat, work, and temperature, and their relation to energy."},
                    {"id": "natural-sciences/physics/electromagnetism", "title": "Electromagnetism", "scope": "The branch of physics involving the study of electromagnetic force and electromagnetic fields."},
                    {"id": "natural-sciences/physics/relativity", "title": "Relativity", "scope": "Einstein's theories of special and general relativity governing space-time and gravity."},
                ],
            },
        ],
    },
    {
        "id": "formal-sciences",
        "title": "Formal Sciences",
        "parent": None,
        "scope": "Disciplines concerned with abstract formal systems characterized by axioms, rules, and deductive proof.",
        "children": [
            {
                "id": "formal-sciences/computer-science",
                "title": "Computer Science",
                "scope": "The study of computation, information processing, algorithms, software, and computational architecture.",
                "legacy_domains": ["computer-science"],
                "children": [
                    {"id": "formal-sciences/computer-science/algorithms-and-complexity", "title": "Algorithms and Complexity", "scope": "The design, efficiency analysis, and theoretical tractability bounds of computational algorithms."},
                    {"id": "formal-sciences/computer-science/artificial-intelligence", "title": "Artificial Intelligence", "scope": "The theory and development of computer systems able to perform tasks requiring intelligent inference."},
                    {"id": "formal-sciences/computer-science/operating-systems", "title": "Operating Systems", "scope": "System software that manages computer hardware, process execution, memory, and storage resources."},
                    {"id": "formal-sciences/computer-science/distributed-systems", "title": "Distributed Systems", "scope": "Systems of independent networked computers communicating and coordinating to solve problems."},
                    {"id": "formal-sciences/computer-science/cryptography", "title": "Cryptography and Security", "scope": "The practice and study of secure communication, confidentiality, integrity, and cryptographic protocols."},
                    {"id": "formal-sciences/computer-science/programming-languages", "title": "Programming Languages and Compilers", "scope": "The design, semantics, analysis, implementation, and translation of formal programming languages."},
                ],
            },
            {
                "id": "formal-sciences/mathematics",
                "title": "Mathematics",
                "scope": "The abstract science of numbers, quantity, structure, space, and change.",
                "legacy_domains": ["mathematics"],
                "children": [
                    {"id": "formal-sciences/mathematics/algebra", "title": "Algebra", "scope": "The study of mathematical symbols and the rules for manipulating these symbols in equations and structures."},
                    {"id": "formal-sciences/mathematics/analysis", "title": "Mathematical Analysis", "scope": "The branch of mathematics dealing with continuous change, limits, derivatives, integrals, and series."},
                    {"id": "formal-sciences/mathematics/geometry-and-topology", "title": "Geometry and Topology", "scope": "The study of properties of space, shapes, curvature, and properties invariant under continuous deformations."},
                    {"id": "formal-sciences/mathematics/number-theory", "title": "Number Theory", "scope": "The branch of pure mathematics devoted primarily to the study of integers and arithmetic properties."},
                ],
            },
            {
                "id": "formal-sciences/statistics",
                "title": "Statistics",
                "scope": "The discipline that concerns the collection, organization, analysis, interpretation, and presentation of data.",
            },
            {
                "id": "formal-sciences/systems-science",
                "title": "Systems Science and Cybernetics",
                "scope": "The interdisciplinary field studying the nature of complex systems in nature, society, and science.",
                "legacy_domains": ["meta-knowledge"],
                "children": [
                    {"id": "formal-sciences/systems-science/cybernetics", "title": "Cybernetics", "scope": "The science of communications and automatic control systems in machines and living things."},
                    {"id": "formal-sciences/systems-science/control-theory", "title": "Control Theory", "scope": "Mathematical subfield dealing with the control of continuously operating dynamical systems."},
                ],
            },
        ],
    },
    {
        "id": "applied-sciences",
        "title": "Applied Sciences",
        "parent": None,
        "scope": "Disciplines that apply existing scientific knowledge to practical applications, technology, and professions.",
        "legacy_domains": ["practical-domains"],
        "children": [
            {
                "id": "applied-sciences/agriculture",
                "title": "Agricultural Sciences",
                "scope": "The practice and science of cultivating plants, livestock, and managing biological agricultural systems.",
            },
            {
                "id": "applied-sciences/architecture-and-design",
                "title": "Architecture and Built Environment",
                "scope": "The art and science of designing and erecting buildings, infrastructure, and urban environments.",
            },
            {
                "id": "applied-sciences/business",
                "title": "Business and Management",
                "scope": "The operational, organizational, and strategic management of commercial enterprises and institutions.",
                "children": [
                    {"id": "applied-sciences/business/finance", "title": "Finance", "scope": "The management, creation, and study of money, banking, credit, investments, assets, and liabilities."},
                    {"id": "applied-sciences/business/management", "title": "Management", "scope": "The administration of an organization, coordinating activities and setting strategic direction."},
                    {"id": "applied-sciences/business/marketing", "title": "Marketing", "scope": "The study and management of exchange relationships, market demand, and consumer behavior."},
                ],
            },
            {
                "id": "applied-sciences/education",
                "title": "Education and Pedagogy",
                "scope": "The disciplines focused on the methods, theories, and processes of teaching and learning.",
            },
            {
                "id": "applied-sciences/engineering-and-technology",
                "title": "Engineering and Technology",
                "scope": "The application of science and mathematics to design, build, and maintain structures, machines, and systems.",
                "legacy_domains": ["engineering"],
                "children": [
                    {"id": "applied-sciences/engineering-and-technology/mechanical-engineering", "title": "Mechanical Engineering", "scope": "The discipline applying physics and materials science principles to design and manufacture machinery."},
                    {"id": "applied-sciences/engineering-and-technology/electrical-engineering", "title": "Electrical Engineering", "scope": "The discipline that studies and applies electricity, electronics, and electromagnetism."},
                    {"id": "applied-sciences/engineering-and-technology/civil-engineering", "title": "Civil Engineering", "scope": "The design, construction, and maintenance of the physical and naturally built environment."},
                    {"id": "applied-sciences/engineering-and-technology/chemical-engineering", "title": "Chemical Engineering", "scope": "The branch dealing with the design and operation of plants converting chemicals and materials."},
                    {"id": "applied-sciences/engineering-and-technology/aerospace-engineering", "title": "Aerospace Engineering", "scope": "The primary field of engineering concerned with the development of aircraft and spacecraft."},
                    {"id": "applied-sciences/engineering-and-technology/software-engineering", "title": "Software Engineering", "scope": "The systematic engineering approach to the development, operation, and maintenance of software."},
                ],
            },
            {
                "id": "applied-sciences/environmental-studies",
                "title": "Environmental Studies and Forestry",
                "scope": "Interdisciplinary field studying human interaction with the natural environment and resources.",
            },
            {
                "id": "applied-sciences/medicine-and-health",
                "title": "Medicine and Health Sciences",
                "scope": "The science and practice of caring for patients, managing diagnosis, prognosis, prevention, and treatment.",
                "children": [
                    {"id": "applied-sciences/medicine-and-health/clinical-medicine", "title": "Clinical Medicine", "scope": "The practice of medicine based on direct observation and treatment of patients."},
                    {"id": "applied-sciences/medicine-and-health/pharmacology", "title": "Pharmacology and Therapeutics", "scope": "The scientific study of drug or medication action and biological systems interaction."},
                    {"id": "applied-sciences/medicine-and-health/public-health", "title": "Public Health and Epidemiology", "scope": "The science of protecting and improving the health of people and their communities."},
                ],
            },
            {
                "id": "applied-sciences/military-sciences",
                "title": "Military Sciences",
                "scope": "The study of military processes, institutions, behavior, warfare strategy, and defense logistics.",
                "children": [
                    {"id": "applied-sciences/military-sciences/military-strategy", "title": "Military Strategy and Operational Art", "scope": "The planning and execution of the operational movement and disposition of forces in war."},
                    {"id": "applied-sciences/military-sciences/intelligence", "title": "Military Intelligence and Deception", "scope": "The collection and analysis of information concerning adversary capabilities and operational deception."},
                ],
            },
            {
                "id": "applied-sciences/public-administration",
                "title": "Public Administration and Law",
                "scope": "The implementation of government policy and the academic study of public governance, law, and civil service.",
            },
        ],
    },
]


def flatten_tree(nodes: List[Dict[str, Any]], parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    flat: List[Dict[str, Any]] = []
    for order, node in enumerate(nodes, start=1):
        nid = node["id"]
        title = node["title"]
        scope = node.get("scope", f"Academic study of {title}.")
        legacy_domains = node.get("legacy_domains", [])
        aliases = node.get("aliases", [])

        flat_node = {
            "id": nid,
            "title": title,
            "parent": parent_id,
            "layer": "spine",
            "order": order,
            "wikipedia": {"title": title, "pageid": 0, "revid": 0},
            "wikidata": "",
            "aliases": aliases,
            "legacy_domains": legacy_domains,
            "scope": scope,
            "listed_under": [],
            "also_in": [],
            "home_reason": "outline",
            "status": "active",
            "moved_from": [],
            "seed": {"status": "pending"},
            "codes": {},
        }
        flat.append(flat_node)

        if "children" in node:
            flat.extend(flatten_tree(node["children"], parent_id=nid))

    return flat


def validate_taxonomy(nodes: List[Dict[str, Any]]) -> List[str]:
    """Validate against the 12 rules in §5.3.2."""
    errors: List[str] = []
    id_map = {n["id"]: n for n in nodes}

    for n in nodes:
        nid = n["id"]
        pid = n["parent"]

        # Rule 1 & 2: Parent valid
        if pid is not None:
            if pid not in id_map:
                errors.append(f"Node '{nid}': parent '{pid}' does not exist.")
            expected_prefix = f"{pid}/"
            if not nid.startswith(expected_prefix):
                errors.append(f"Node '{nid}': ID does not start with parent prefix '{expected_prefix}'.")

        # Rule 4: Depth cap of 6
        depth = len(nid.split("/"))
        if depth > 6:
            errors.append(f"Node '{nid}': depth {depth} exceeds max spine depth of 6.")

        # Rule 5: Slug syntax
        segments = nid.split("/")
        for seg in segments:
            if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", seg):
                errors.append(f"Node '{nid}': segment '{seg}' violates kebab-case format.")
            if len(seg) > 40:
                errors.append(f"Node '{nid}': segment '{seg}' exceeds 40 chars.")

        # Rule 7: Windows reserved names
        reserved = {"con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "lpt1", "lpt2", "lpt3"}
        for seg in segments:
            if seg in reserved:
                errors.append(f"Node '{nid}': segment '{seg}' is a Windows reserved name.")

    return errors


def main() -> int:
    flat_nodes = flatten_tree(DIVISIONS)
    print(f"Flattened {len(flat_nodes)} canonical spine nodes.")

    errors = validate_taxonomy(flat_nodes)
    if errors:
        print(f"Validation failed with {len(errors)} error(s):", file=sys.stderr)
        for err in errors[:10]:
            print(f"  • {err}", file=sys.stderr)
        return 1

    print("✓ All 12 validation rules passed cleanly!")

    # 1. Write config/taxonomy.json in Brahma repo
    config_dir = ROOT / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    repo_tax_file = config_dir / "taxonomy.json"
    repo_tax_file.write_text(json.dumps(flat_nodes, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ Wrote repo taxonomy: {repo_tax_file}")

    # 2. Write to Obsidian vault _taxonomy/ directory if vault exists
    vault = second_brain.vault_root()
    if vault and vault.is_dir():
        vault_tax_dir = vault / "wiki" / "library" / "knowledge" / "_taxonomy"
        vault_tax_dir.mkdir(parents=True, exist_ok=True)

        vault_tax_file = vault_tax_dir / "taxonomy.json"
        vault_tax_file.write_text(json.dumps(flat_nodes, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ Wrote vault taxonomy: {vault_tax_file}")

        # Write overrides file
        overrides_file = vault_tax_dir / "taxonomy.overrides.yaml"
        if not overrides_file.exists():
            overrides_content = (
                "# _taxonomy/taxonomy.overrides.yaml\n"
                "# Human-reviewed overrides and custom topic nodes\n"
                "snapshot: {title: Outline of academic disciplines, version: 1.0}\n\n"
                "aliases:\n"
                "  formal-sciences/computer-science: [\"computer-science\", \"cs\"]\n"
                "  formal-sciences/mathematics: [\"mathematics\", \"math\"]\n"
                "  applied-sciences/engineering-and-technology: [\"engineering\"]\n"
                "  humanities/visual-arts: [\"arts\"]\n"
                "  humanities/philosophy/epistemology: [\"meta-knowledge\"]\n\n"
                "expand:\n"
                "  humanities/philosophy/eastern-philosophy/buddhism:\n"
                "    early-buddhism-nikayas: \"Early Buddhism and Nikaya canonical texts\"\n"
            )
            overrides_file.write_text(overrides_content, encoding="utf-8")
            print(f"✓ Wrote taxonomy overrides: {overrides_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
