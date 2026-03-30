# Semiconductor Technology Reference: GAA, CPO, TGV

Technical reference library for the Semi Industry Analysis skill. The Evaluator uses this document to verify technical accuracy of generated reports.

---

## 1. Gate-All-Around (GAA) / GAAFET

### Definition
Gate-All-Around Field-Effect Transistor (GAAFET) is the successor to FinFET architecture. Instead of a fin-shaped channel with the gate wrapping three sides, GAA uses horizontally stacked nanosheets (or nanowires) where the gate material fully surrounds the channel on all four sides, providing superior electrostatic control.

### Key Technical Parameters

| Parameter | Description | Benchmark Values |
|:---|:---|:---|
| **Nanosheet Width** | Width of each horizontal channel sheet | 5-12 nm typical; wider sheets = more drive current |
| **Sheet Count** | Number of stacked nanosheets per transistor | 3-4 sheets (current gen); 5+ sheets (future) |
| **Gate Length (Lg)** | Physical gate length | < 12 nm at 3nm node |
| **Contacted Poly Pitch (CPP)** | Transistor-to-transistor spacing | 48-51 nm (3nm class) |
| **Minimum Metal Pitch (MMP)** | Tightest metal interconnect spacing | 21-24 nm (3nm class) |
| **Drive Current (Idsat)** | ON-state saturation current | Target > 2 mA/um (NMOS) |
| **Leakage Current (Ioff)** | OFF-state leakage | Target < 1 nA/um |
| **Vdd** | Operating voltage | 0.65-0.75V typical |

### FinFET vs GAA Comparison

| Aspect | FinFET | GAA (Nanosheet) |
|:---|:---|:---|
| Gate Control | 3-sided (tri-gate) | 4-sided (all-around) |
| Channel Shape | Vertical fin | Horizontal stacked sheets |
| Width Quantization | Fixed by fin height | Adjustable by sheet width |
| Power Efficiency | Good | ~15-25% better at same perf |
| Scaling Limit | ~3nm node | Extends to sub-2nm |
| Manufacturing Complexity | Established | Inner spacer, sheet release steps |

### Big Three GAA Roadmaps

- **TSMC**:
  - N3 / N3E / N3P: Still FinFET (production 2022-2024)
  - N2: First GAA (nanosheet), targeted HVM 2025
  - N2P / A16: Backside power delivery (BSPDN) + GAA, 2026+
- **Samsung**:
  - 3nm GAA (1st gen): Production started 2022, initially low yield
  - 2nm GAA (2nd gen): Targeted 2025, improved nanosheet stacking
  - SF2 (2nd gen 2nm): Enhanced performance, 2026
- **Intel**:
  - Intel 20A: RibbonFET (Intel's GAA) + PowerVia (backside power), targeted 2024
  - Intel 18A: Refined RibbonFET, external foundry customers, 2025
  - Intel 14A: Next-gen GAA, 2026+

### Key Manufacturing Challenges
- **Inner Spacer Formation**: Precise etching of SiGe sacrificial layers between nanosheets without damaging Si channels
- **Sheet Release**: Selective removal of SiGe to free Si nanosheets; uniformity across wafer is critical
- **Nanosheet Width Uniformity**: Variation in sheet width directly impacts Vt (threshold voltage) uniformity
- **High-k / Metal Gate Fill**: Filling the narrow gap between stacked sheets with gate dielectric and work-function metals
- **Yield**: Samsung's early 3nm GAA reportedly had ~20% yield initially vs ~80% for TSMC's N3 FinFET

---

## 2. Co-Packaged Optics (CPO)

### Definition
Co-Packaged Optics integrates optical I/O engines directly onto or adjacent to the switch/compute ASIC package, replacing traditional pluggable optical transceivers. This dramatically reduces power consumption for data movement and increases bandwidth density by shortening the electrical trace length between the chip and the optical engine.

### Key Technical Parameters

| Parameter | Description | Benchmark Values |
|:---|:---|:---|
| **Bandwidth per Port** | Data rate per optical lane | 100G (current), 200G (next-gen) per lane |
| **Total Package Bandwidth** | Aggregate optical I/O | 51.2 Tbps (current switches), 102.4 Tbps (next-gen) |
| **Power Efficiency** | Energy per bit for optical link | Target < 5 pJ/bit (vs 15-20 pJ/bit for pluggable) |
| **Reach** | Optical link distance | 100m - 2km (intra-datacenter) |
| **Wavelength** | Optical signal wavelength | 1310 nm (O-band) typical for datacenter |
| **Modulation** | Signal modulation format | PAM4, coherent for longer reach |
| **Fiber Density** | Fibers per package edge | 32-64 fibers per CPO module |

### CPO vs Pluggable Optics Comparison

| Aspect | Pluggable Transceivers | Co-Packaged Optics |
|:---|:---|:---|
| Integration | Separate modules on faceplate | On-package or in-package |
| Power per bit | 15-20 pJ/bit | < 5 pJ/bit target |
| Bandwidth Density | Limited by faceplate space | 3-5x higher density |
| Serviceability | Hot-swappable | Requires package-level replacement |
| Thermal Management | Self-contained cooling | Shared with ASIC thermal solution |
| Standardization | Mature (QSFP-DD, OSFP) | Emerging (OIF, CW-WDM MSA) |

### Big Three CPO Approaches

- **TSMC**:
  - InFO_SoW (System-on-Wafer): Wafer-level integration enabling CPO placement adjacent to compute die
  - COUPE (Compact Universal Photonic Engine): Silicon photonics integration platform
  - CoWoS with photonic interposer: 2.5D integration of optical engines on silicon interposer
- **Intel**:
  - Integrated Photonics: In-house silicon photonics fab (previously Intel Silicon Photonics)
  - Foveros 3D stacking: Enables vertical integration of photonic chiplets
  - EMIB (Embedded Multi-die Interconnect Bridge): Heterogeneous die-to-die optical connectivity
- **Samsung**:
  - I-Cube (Interposer-Cube): 2.5D integration platform for photonic chiplets
  - FOPLP (Fan-Out Panel Level Packaging): Large-area packaging for CPO integration
  - Silicon photonics partnership approach (less vertically integrated than Intel)

### Key Technical Challenges
- **Thermal Crosstalk**: Laser wavelength shifts with temperature; co-location with hot ASIC die requires precise thermal management
- **Fiber Attach**: Mechanical alignment of single-mode fibers to photonic chip waveguides (submicron tolerance)
- **Laser Integration**: External laser source (ELS) vs on-chip laser; reliability concerns for on-chip
- **Testing**: Known-good-die (KGD) testing of photonic components before integration
- **Standardization**: No single industry standard for CPO form factor or electrical interface

---

## 3. Through Glass Via (TGV)

### Definition
Through Glass Via (TGV) is an advanced packaging interconnect technology that creates vertical electrical pathways through glass substrates, analogous to Through Silicon Via (TSV) in silicon interposers but using glass as the carrier material. Glass substrates offer superior electrical properties, dimensional stability, and potential for large-area panel-level processing.

### Key Technical Parameters

| Parameter | Description | Benchmark Values |
|:---|:---|:---|
| **Via Diameter** | TGV hole diameter | 20-50 um (laser-drilled); 5-10 um (etch-based) |
| **Via Pitch** | Center-to-center via spacing | 40-100 um typical |
| **Glass Thickness** | Substrate thickness | 100-300 um (thin); 500+ um (structural) |
| **Via Aspect Ratio** | Depth-to-diameter ratio | 5:1 to 10:1 achievable |
| **RDL Line/Space** | Redistribution layer resolution | 2/2 um (advanced); 5/5 um (standard) |
| **CTE** | Coefficient of Thermal Expansion | ~3.2 ppm/K (glass) vs ~2.6 ppm/K (silicon) |
| **Dielectric Constant (Dk)** | Electrical insulation property | ~5.0 (glass) vs ~11.7 (silicon) — lower is better for signal integrity |
| **Loss Tangent (Df)** | Signal loss factor | ~0.004 (glass) vs ~0.01 (silicon) — lower is better |

### TGV vs TSV vs Organic Substrate Comparison

| Aspect | TSV (Silicon) | TGV (Glass) | Organic Substrate |
|:---|:---|:---|:---|
| Substrate Material | Silicon wafer | Borosilicate/alkali-free glass | BT/ABF resin |
| Dielectric Constant | 11.7 (high) | ~5.0 (medium) | ~3.5 (low) |
| Signal Loss | Higher (conductive Si) | Lower (insulating glass) | Lowest |
| CTE Match to Si Die | Excellent (~2.6) | Good (~3.2) | Poor (~15-17) |
| Thermal Conductivity | 150 W/mK (excellent) | ~1 W/mK (poor) | ~0.3 W/mK (poor) |
| Warpage Control | Good (stiff) | Excellent (low CTE, tunable) | Challenging at large sizes |
| Panel-Level Processing | No (wafer only) | Yes (Gen 5+ glass panels) | Yes |
| Cost at Scale | High (wafer-based) | Medium (panel-based potential) | Low |
| Via Density | Very high (1-5 um pitch) | High (10-50 um pitch) | Low (>100 um pitch) |

### Big Three TGV Approaches

- **TSMC**:
  - Evaluating glass interposers as next-gen replacement for CoWoS silicon interposers
  - Focus on large-area glass panels for chiplet integration at lower cost than silicon
  - R&D partnerships with glass substrate suppliers (Corning, AGC, Schott)
- **Intel**:
  - Glass substrate core technology program announced 2023
  - Targeting 50% higher interconnect density vs current organic substrates
  - Aims for glass substrate production by 2026-2028 timeframe
  - Focus on power delivery and signal integrity improvements
- **Samsung**:
  - Glass substrate R&D for next-gen 2.5D/3D packaging
  - Exploring glass interposer + FOPLP combination
  - Working with glass substrate ecosystem partners

### Key Technical Challenges
- **Laser Drilling Quality**: High-power laser via formation can create micro-cracks and heat-affected zones in glass
- **Via Metallization**: Conformal copper plating inside high-aspect-ratio glass vias (adhesion to glass is harder than to silicon)
- **Glass Handling**: Thin glass (< 200 um) is fragile; requires carrier bonding/debonding for wafer-level processing
- **CTE Mismatch Management**: Glass CTE (~3.2 ppm/K) is close to but not identical to silicon (~2.6 ppm/K); thermal cycling reliability must be validated
- **RDL on Glass**: Forming fine-pitch redistribution layers on glass surface (adhesion, planarity)
- **Thermal Dissipation**: Glass is a poor thermal conductor (~1 W/mK vs 150 W/mK for silicon); requires thermal via arrays or metallic heat spreading layers
- **Supply Chain Maturity**: Glass substrate ecosystem is nascent compared to silicon interposer and organic substrate supply chains
- **Cost Crossover**: TGV becomes cost-competitive with TSV only at large panel sizes (> 510mm x 515mm Gen 5 panels)

---

## 4. Cross-Technology Interactions

### GAA + CPO Synergy
- GAA transistors in SerDes IP enable higher-speed electrical interfaces to CPO engines
- Lower power GAA I/O drivers reduce total link power budget, making CPO economics more favorable
- TSMC N2 GAA process expected to offer optimized silicon photonics PDK

### GAA + TGV Synergy
- GAA chiplets on glass interposers can achieve higher interconnect density than organic substrates
- Glass substrate's low signal loss benefits high-speed die-to-die links from GAA chiplets
- Backside power delivery (BSPDN) in GAA processes complements TGV power distribution

### CPO + TGV Synergy
- Glass interposers can integrate optical waveguides alongside electrical TGVs
- Low dielectric constant of glass reduces crosstalk between optical and electrical signals
- Panel-level processing of glass enables cost-effective integration of photonic and electronic chiplets

---

## 5. Key Metrics for Competitive Comparison

When evaluating the Big Three, the report should assess:

1. **GAA Progress**: Yield rate, production volume (wafers/month), customer tape-outs, node-to-node PPA (Power/Performance/Area) improvement
2. **CPO Layout**: Integration architecture maturity, demonstrated bandwidth, power efficiency (pJ/bit), customer design-ins, ecosystem partnerships
3. **TGV Adoption**: Via density achieved, glass panel size, RDL resolution, reliability qualification status, supply chain readiness, cost projection vs TSV/organic
