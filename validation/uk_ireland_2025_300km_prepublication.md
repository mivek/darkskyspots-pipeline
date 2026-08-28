# Audit prépublication — cascade GeoNames

- Pays : `GB`, `IE`
- Bbox : `[-11.0, 49.0, 2.0, 55.0]`
- Spots analysés : **1179**
- Divergences runtime/audit : **0**

## Darkness

| Corpus | Valides | Invalides | Min | P25 | Médiane | P75 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| Global | 1179 | 0 | 0.0 | 0.357887 | 0.595121 | 0.794211 | 1.0 |
| GB | 821 | 0 | 0.0 | 0.335583 | 0.49379 | 0.718628 | 1.0 |
| IE | 358 | 0 | 0.0 | 0.721693 | 0.796778 | 0.942781 | 1.0 |

### Bins fixes de darkness (0,1)

| Corpus | 0.0-0.1 | 0.1-0.2 | 0.2-0.3 | 0.3-0.4 | 0.4-0.5 | 0.5-0.6 | 0.6-0.7 | 0.7-0.8 | 0.8-0.9 | 0.9-1.0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Global | 41 | 91 | 49 | 155 | 161 | 95 | 93 | 212 | 130 | 152 |
| GB | 40 | 87 | 48 | 144 | 138 | 77 | 70 | 111 | 59 | 47 |
| IE | 1 | 4 | 1 | 11 | 23 | 18 | 23 | 101 | 71 | 105 |

## Emprise ALR valide

- Raster : `/tmp/darkskyspots-uk-ireland-YrMiHj/debug_darkness_uk_ireland_2025.tif`
- Pixels finis : **8519416 / 15184876**
- Emprise WGS84 : `[-16.058333333333337, 46.775, 3.5208333333333286, 57.3]`
- Couverture des bords : `{'west': True, 'south': True, 'east': True, 'north': True, 'all': True}`

## Distances de la cascade

| Pays | <5 km | 5–25 km | 25–40 km | ADM2 | ADM1 |
|---|---:|---:|---:|---:|---:|
| `GB` | 817 | 4 | 0 | 0 | 0 |
| `IE` | 358 | 0 | 0 | 0 | 0 |
| **Global** | 1175 | 4 | 0 | 0 | 0 |

## Gagnants par code

| Pays | Code | Spots |
|---|---|---:|
| `GB` | `CAPE` | 3 |
| `GB` | `FRST` | 1 |
| `GB` | `HDLD` | 1 |
| `GB` | `ISL` | 10 |
| `GB` | `LK` | 11 |
| `GB` | `MT` | 15 |
| `GB` | `MTS` | 2 |
| `GB` | `PK` | 3 |
| `GB` | `PPL` | 691 |
| `GB` | `PPLA2` | 4 |
| `GB` | `PPLA3` | 26 |
| `GB` | `PPLA4` | 1 |
| `GB` | `PPLL` | 43 |
| `GB` | `PRK` | 2 |
| `GB` | `RESN` | 1 |
| `GB` | `RGN` | 3 |
| `GB` | `RSV` | 4 |
| `IE` | `CAPE` | 3 |
| `IE` | `FRST` | 2 |
| `IE` | `ISL` | 3 |
| `IE` | `LCTY` | 66 |
| `IE` | `LK` | 31 |
| `IE` | `LKS` | 3 |
| `IE` | `MT` | 23 |
| `IE` | `MTS` | 3 |
| `IE` | `PPL` | 170 |
| `IE` | `PPLL` | 51 |
| `IE` | `PRK` | 3 |

## Contrôle des pays

- Codes inattendus : `{}`
- IM/JE/GG : `{'IM': 0, 'JE': 0, 'GG': 0}`

## Îles

| Île | Pays | Natural Earth 1:10m | Spots proches | Statut |
|---|---|---|---:|---|
| Isle of Wight | `GB` | oui | 3 | `covered_with_spots` |
| Anglesey | `GB` | oui | 3 | `covered_with_spots` |
| Isles of Scilly | `GB` | oui | 1 | `covered_with_spots` |
| Lundy | `GB` | oui | 0 | `covered_by_natural_earth_no_spot` |
| Isle of Sheppey | `GB` | oui | 2 | `covered_with_spots` |
| Achill Island | `IE` | oui | 3 | `covered_with_spots` |
| Valentia Island | `IE` | oui | 1 | `covered_with_spots` |
| Arranmore | `IE` | oui | 1 | `covered_with_spots` |

Dépendances de la Couronne volontairement hors périmètre : `IM`, `JE`, `GG`.

- `IM` Isle of Man : Natural Earth=oui, statut=`out_of_scope_crown_dependency`.
- `JE` Jersey : Natural Earth=oui, statut=`out_of_scope_crown_dependency`.
- `GG` Guernsey : Natural Earth=oui, statut=`out_of_scope_crown_dependency`.

Spots situés spatialement dans une dépendance interdite (identifiants uniquement) :

- `GG` : aucun
- `IM` : aucun
- `JE` : aucun
## Divergences runtime/audit

Aucune divergence.

## Échantillon déterministe

| Pays | Tier | ID | Runtime name | Audit name | Runtime code | Runtime distance km | Audit code | Audit distance km | near | darkness |
|---|---|---|---|---|---|---:|---|---:|---|---:|
| GB | under_5 | 50.1396_-5.6979 | Cape Cornwall | Cape Cornwall | CAPE | 0.711 | CAPE | 0.711 | St Just | 1.0 |
| GB | under_5 | 51.0604_-0.2438 | Saint Leonards Forest | Saint Leonards Forest | FRST | 0.821 | FRST | 0.821 | Handcross | 0.22134519008429887 |
| GB | under_5 | 50.0063_-5.2313 | Predannack Head | Predannack Head | HDLD | 1.510 | HDLD | 1.510 | Mullion | 0.9530803916753927 |
| GB | under_5 | 50.6479_-1.9854 | Drove Island | Drove Island | ISL | 2.058 | ISL | 2.058 | Swanage | 0.4745726145250534 |
| GB | under_5 | 51.3354_-2.6146 | Chew Valley Lake | Chew Valley Lake | LK | 0.147 | LK | 0.147 | Bishop Sutton | 0.3458091458685415 |
| GB | under_5 | 50.5979_-3.8438 | Hameldown | Hameldown | MT | 1.191 | MT | 1.191 | Chagford | 0.7363431765057739 |
| GB | under_5 | 51.8813_-3.4521 | Brecon Beacons | Brecon Beacons | MTS | 0.561 | MTS | 0.561 | Brecon | 0.6040807627473002 |
| GB | under_5 | 53.6688_-2.2563 | Whittle Pike | Whittle Pike | PK | 0.707 | PK | 0.707 | Rossendale | 0.09329291709228649 |
| GB | under_5 | 49.9562_-6.3396 | New Grimsby | New Grimsby | PPL | 0.076 | PPL | 0.076 | Isles of Scilly | 1.0 |
| GB | under_5 | 52.7979_-2.1229 | Stafford | Stafford | PPLA2 | 0.923 | PPLA2 | 0.923 | Stafford | 0.19673617860661974 |
| GB | under_5 | 51.3312_-1.6646 | Burbage | Burbage | PPLA3 | 2.331 | PPLA3 | 2.331 | Burbage | 0.4994582051771608 |
| GB | under_5 | 51.9188_-2.0813 | Swindon | Swindon | PPLA4 | 1.295 | PPLA4 | 1.295 | Swindon | 0.33938889785939996 |
| GB | under_5 | 50.5521_-4.6438 | Bradford | Bradford | PPLL | 0.994 | PPLL | 0.994 | Helland | 0.8424820229735033 |
| GB | under_5 | 51.3813_-2.4021 | Brickfields Park | Brickfields Park | PRK | 1.165 | PRK | 1.165 | Corston | 0.32428036652886627 |
| GB | under_5 | 50.9229_0.9354 | Dungeness National Nature Reserve | Dungeness National Nature Reserve | RESN | 1.129 | RESN | 1.129 | Lydd | 0.568251521529463 |
| GB | under_5 | 51.4229_-0.2896 | Richmond and Kingston | Richmond and Kingston | RGN | 0.661 | RGN | 0.661 | Kingston upon Thames | 0.0 |
| GB | under_5 | 52.2021_-3.5896 | Dol-y-mynach Reservoir | Dol-y-mynach Reservoir | RSV | 3.546 | RSV | 3.546 | Newbridge on Wye | 0.8518142156831228 |
| IE | under_5 | 51.6562_-8.6771 | Flaxfort Head | Flaxfort Head | CAPE | 1.784 | CAPE | 1.784 | Courtmacsherry | 0.7209862046567956 |
| IE | under_5 | 52.2062_-9.2188 | Meendurragha | Meendurragha | FRST | 0.579 | FRST | 0.579 | Rathmore | 0.8958468692742085 |
| IE | under_5 | 52.2062_-6.6313 | Cull Island | Cull Island | ISL | 0.365 | ISL | 0.365 | Taghmon | 0.8475931842010128 |
| IE | under_5 | 51.6104_-8.9313 | Carhoo | Carhoo | LCTY | 0.142 | LCTY | 0.142 | Clonakilty | 0.8194914216358021 |
| IE | under_5 | 51.5396_-9.1396 | Lough Clubir | Lough Clubir | LK | 0.930 | LK | 0.930 | Ross Carbery | 1.0 |
| IE | under_5 | 52.0229_-9.4146 | Doo Loughs | Doo Loughs | LKS | 0.727 | LKS | 0.727 | Killarney | 0.7385667303713831 |
| IE | under_5 | 51.7188_-9.3188 | Mullaghmesha | Mullaghmesha | MT | 1.194 | MT | 1.194 | Bantry | 1.0 |
| IE | under_5 | 52.1938_-9.7771 | Slieve Mish Mountains | Slieve Mish Mountains | MTS | 0.993 | MTS | 0.993 | Baile an Mhuilinn | 0.8342890839825252 |
| IE | under_5 | 51.5646_-9.0229 | Rosscarbery | Rosscarbery | PPL | 1.409 | PPL | 1.409 | Ross Carbery | 0.9434725205023389 |
| IE | under_5 | 51.4688_-9.7771 | Dough | Dough | PPLL | 0.723 | PPLL | 0.723 | Schull | 1.0 |
| IE | under_5 | 51.7479_-8.8604 | Kilcolman Park | Kilcolman Park | PRK | 1.821 | PRK | 1.821 | Enniskeane | 0.7266035934078271 |
| GB | under_5 | 50.0521_-5.1979 | Cross Lanes | Cross Lanes | PPL | 2.118 | PPL | 2.118 | Mullion | 0.8150645368058178 |
| GB | under_5 | 50.0979_-5.5604 | Tredavoe | Tredavoe | PPL | 0.549 | PPL | 0.549 | Mousehole | 0.848259673736468 |
| GB | under_5 | 50.1437_-5.3188 | Nancegollan | Nancegollan | PPL | 0.553 | PPL | 0.553 | Wendron | 0.7300026033013061 |
| GB | under_5 | 50.1854_-5.6063 | Treen | Treen | PPL | 0.502 | PPL | 0.502 | Madron | 0.9568192780630721 |
| GB | under_5 | 50.2354_-3.6854 | South Allington | South Allington | PPL | 0.566 | PPL | 0.566 | Salcombe | 0.8509831726591195 |
| GB | under_5 | 50.2354_-3.7313 | Goodshelter | Goodshelter | PPL | 0.218 | PPL | 0.218 | Salcombe | 0.8365438028455401 |
| GB | under_5 | 50.2354_-4.8313 | Tregavarras | Tregavarras | PPL | 0.448 | PPL | 0.448 | Gorran Haven | 0.7946691493355107 |
| GB | under_5 | 50.2354_-4.9688 | Lamorran | Lamorran | PPL | 0.794 | PPL | 0.794 | Tregoney | 0.7046466914296323 |
| GB | under_5 | 50.3229_-5.1938 | Trevellas | Trevellas | PPL | 1.269 | PPL | 1.269 | Saint Agnes | 0.7264282041235859 |
| GB | under_5 | 50.3271_-3.7313 | East Allington | East Allington | PPL | 0.393 | PPL | 0.393 | Loddiswell | 0.7381490059807475 |
| GB | under_5 | 50.3271_-3.9604 | Battisborough Cross | Battisborough Cross | PPL | 1.171 | PPL | 1.171 | Yealmpton | 0.5710903437947235 |
| GB | under_5 | 50.3271_-4.5646 | Lansallos | Lansallos | PPL | 1.070 | PPL | 1.070 | Polperro | 0.76776091024957 |
| GB | under_5 | 50.3271_-4.6479 | Polruan | Polruan | PPL | 1.170 | PPL | 1.170 | Polruan | 0.7306136237827912 |
| GB | under_5 | 50.3479_-5.1479 | Perranporth | Perranporth | PPL | 0.713 | PPL | 0.713 | Perranporth | 0.740301394665955 |
| GB | under_5 | 50.3729_-4.4146 | No Mans Land Looe | No Mans Land Looe | PPL | 1.385 | PPL | 1.385 | Looe | 0.6907610718441582 |
| GB | under_5 | 50.4146_-4.0979 | Eggbuckland | Eggbuckland | PPL | 2.097 | PPL | 2.097 | Plympton | 0.30632456786350193 |
| GB | under_5 | 50.4146_-4.2771 | Trematon | Trematon | PPL | 1.036 | PPL | 1.036 | Landrake | 0.476425863004193 |
| GB | 5_to_25 | 52.3563_-3.5896 | Craig-yr-allt gôch Reservoir | Craig-yr-allt gôch Reservoir | RSV | 5.311 | RSV | 5.311 | Rhayader | 0.8749082950047748 |
| GB | 5_to_25 | 54.1271_-1.9396 | Great Whernside | Great Whernside | MT | 5.375 | MT | 5.375 | Grassington | 0.49416585177772476 |
| GB | 5_to_25 | 54.2229_-6.7979 | Skerries | Skerries | PPL | 5.382 | PPL | 5.382 | Keady | 0.6914317443356355 |
| GB | 5_to_25 | 54.8604_-5.9854 | Douglas Top | Douglas Top | MT | 5.008 | MT | 5.008 | Larne | 0.5430926133979371 |
| IE | under_5 | 51.6063_-10.1438 | Garnish | Garnish | PPL | 1.000 | PPL | 1.000 | Castletownbere | 1.0 |
| IE | under_5 | 51.6063_-9.4563 | Coosane | Coosane | PPL | 0.892 | PPL | 0.892 | Bantry | 1.0 |
| IE | under_5 | 51.6063_-9.6854 | Gortnakilly | Gortnakilly | PPL | 1.090 | PPL | 1.090 | Schull | 1.0 |
| IE | under_5 | 51.6313_-9.2729 | Madore | Madore | LCTY | 1.599 | LCTY | 1.599 | Skibbereen | 0.9902434999437717 |
| IE | under_5 | 51.6562_-8.7229 | Flaxfort Head | Flaxfort Head | CAPE | 1.404 | CAPE | 1.404 | Courtmacsherry | 0.7401436316248223 |
| IE | under_5 | 51.6979_-9.9604 | Bofickil | Bofickil | PPL | 1.178 | PPL | 1.178 | Castletownbere | 1.0 |
| IE | under_5 | 51.7021_-8.4688 | Ringville | Ringville | LCTY | 0.628 | LCTY | 0.628 | Kinsale | 0.5748898782441023 |
| IE | under_5 | 51.7437_-9.6854 | Knockeirky | Knockeirky | MT | 0.473 | MT | 0.473 | Kenmare | 1.0 |
| IE | under_5 | 51.7479_-8.3604 | Ballyfoyle | Ballyfoyle | PPL | 0.748 | PPL | 0.748 | Fountainstown | 0.4960456854959191 |
| IE | under_5 | 51.7812_-9.1813 | Togher Bridge | Togher Bridge | PPLL | 0.862 | PPLL | 0.862 | Dunmanway | 0.9333774285254792 |
| IE | under_5 | 51.7938_-8.6313 | Barna Cross Roads | Barna Cross Roads | PPLL | 0.872 | PPLL | 0.872 | Innishannon | 0.5045336029148367 |
| IE | under_5 | 51.7938_-9.0438 | Geara Bridge | Geara Bridge | PPLL | 1.368 | PPLL | 1.368 | Enniskeane | 0.8448146369233129 |
| IE | under_5 | 51.8354_-9.8688 | Derrylough | Derrylough | LCTY | 1.728 | LCTY | 1.728 | Kenmare | 1.0 |
| GB | under_5 | 50.4604_-4.0521 | Shaugh Prior | Shaugh Prior | PPL | 1.108 | PPL | 1.108 | Yelverton | 0.4740332074703445 |
| GB | under_5 | 50.4604_-4.9188 | Talskiddy | Talskiddy | PPL | 2.124 | PPL | 2.124 | Saint Columb Major | 0.7382717836888892 |
| GB | under_5 | 50.4646_-3.8104 | Deancombe | Deancombe | PPL | 0.690 | PPL | 0.690 | Buckfastleigh | 0.6821158099134159 |
| GB | under_5 | 50.4729_-3.5021 | Babbacombe | Babbacombe | PPL | 1.245 | PPL | 1.245 | Torquay | 0.5434583763011459 |
| GB | under_5 | 50.5063_-4.6479 | Millpool | Millpool | PPL | 0.448 | PPL | 0.448 | Helland | 0.7385315020840453 |
| GB | under_5 | 50.5063_-5.0104 | Porthcothan | Porthcothan | PPL | 0.610 | PPL | 0.610 | Padstow | 0.8230227975621061 |
| GB | under_5 | 50.5521_-4.3688 | Bray Shop | Bray Shop | PPL | 1.060 | PPL | 1.060 | South Hill | 0.7371404877363695 |
| GB | under_5 | 50.5521_-4.9604 | Crugmeer | Crugmeer | PPL | 0.322 | PPL | 0.322 | Padstow | 0.8770824261785024 |
| GB | under_5 | 50.5979_-4.0979 | Horndon | Horndon | PPL | 0.554 | PPL | 0.554 | Mary Tavy | 0.7357792976793844 |
| GB | under_5 | 50.5979_-4.2479 | Milton Abbot | Milton Abbot | PPL | 0.740 | PPL | 0.740 | Lamerton | 0.7573342892283272 |
| GB | under_5 | 50.5979_-4.5063 | Fivelanes | Fivelanes | PPL | 0.262 | PPL | 0.262 | Trewen | 0.8529853815102315 |
| GB | under_5 | 50.6021_-1.3438 | Atherfield Green | Atherfield Green | PPL | 1.334 | PPL | 1.334 | Chale | 0.570671427771364 |
| GB | under_5 | 50.6021_-2.0771 | Kingston | Kingston | PPL | 1.909 | PPL | 1.909 | Corfe Castle | 0.6066658747976517 |
| GB | under_5 | 50.6313_-3.5896 | Trusham | Trusham | PPL | 2.280 | PPL | 2.280 | Chudleigh | 0.5879052262155983 |
| GB | under_5 | 50.6437_-4.4604 | Badharlick | Badharlick | PPL | 0.785 | PPL | 0.785 | Trewen | 0.839360609791564 |
| GB | under_5 | 50.6437_-4.7354 | Trewarmett | Trewarmett | PPL | 0.192 | PPL | 0.192 | Delabole | 0.9418249080135401 |
| GB | under_5 | 50.6479_-1.1688 | Sandown | Sandown | PPL | 0.680 | PPL | 0.680 | Sandown | 0.484002806526623 |
| GB | under_5 | 50.6479_-2.3396 | Holworth | Holworth | PPL | 0.762 | PPL | 0.762 | Overcombe | 0.6503152445375122 |
| GB | under_5 | 50.6479_-3.3646 | Knowle | Knowle | PPL | 2.013 | PPL | 2.013 | East Budleigh | 0.5659681607317018 |
| GB | under_5 | 50.6938_-1.5313 | Colwell | Colwell | PPL | 0.703 | PPL | 0.703 | Totland | 0.4906922830535684 |
| GB | under_5 | 50.6938_-2.6646 | Swyre | Swyre | PPL | 0.424 | PPL | 0.424 | Burton Bradstock | 0.7218040380195565 |
| GB | under_5 | 50.6938_-3.1354 | Branscombe | Branscombe | PPL | 0.363 | PPL | 0.363 | Beer | 0.727136558685594 |
| GB | under_5 | 50.7021_-3.8813 | Throwleigh | Throwleigh | PPL | 0.394 | PPL | 0.394 | South Zeal | 0.7878985742610329 |
| GB | under_5 | 50.7354_-3.5896 | Whitestone | Whitestone | PPL | 1.216 | PPL | 1.216 | Newton St Cyres | 0.49487334750012046 |
| GB | under_5 | 50.7354_-3.7729 | Hittisleigh | Hittisleigh | PPL | 1.920 | PPL | 1.920 | Cheriton Bishop | 0.7377009385703642 |
| GB | under_5 | 50.7354_-4.0938 | Thorndon Cross | Thorndon Cross | PPL | 1.432 | PPL | 1.432 | Okehampton | 0.8476195928282932 |
| GB | under_5 | 50.7354_-4.3479 | Lana | Lana | PPL | 0.891 | PPL | 0.891 | Boyton | 0.9043369119805178 |
| GB | under_5 | 50.7396_-0.7979 | Selsey | Selsey | PPL | 0.765 | PPL | 0.765 | Selsey | 0.4560856920589591 |
| GB | under_5 | 50.7396_-1.1813 | Ryde | Ryde | PPL | 1.719 | PPL | 1.719 | Ryde | 0.3406244413570203 |
| GB | under_5 | 50.7396_-2.8938 | Charmouth | Charmouth | PPL | 0.485 | PPL | 0.485 | Charmouth | 0.7306927270003594 |
| GB | under_5 | 50.7604_-4.5979 | Tresmorn | Tresmorn | PPLL | 1.472 | PPLL | 1.472 | Poundstock | 0.9748221002019881 |
| GB | under_5 | 50.7812_-1.9854 | Wimborne Minster | Wimborne Minster | PPL | 0.274 | PPL | 0.274 | Wimborne Minster | 0.34402110118450024 |
| GB | under_5 | 50.7812_-2.1229 | Almer | Almer | PPL | 0.939 | PPL | 0.939 | Sturminster Marshall | 0.522959538024149 |
| GB | under_5 | 50.7812_-3.5438 | Brampford Speke | Brampford Speke | PPL | 1.572 | PPL | 1.572 | Stoke Canon | 0.5261428509394113 |
| GB | under_5 | 50.7854_-2.4896 | Nether Cerne | Nether Cerne | PPL | 1.461 | PPL | 1.461 | Cerne Abbas | 0.6745339059358407 |
| GB | under_5 | 50.7854_0.1687 | West Dean | West Dean | PPL | 1.105 | PPL | 1.105 | Alfriston | 0.46723400604245713 |
| GB | under_5 | 50.8271_-2.0771 | Shapwick | Shapwick | PPL | 1.238 | PPL | 1.238 | Sturminster Marshall | 0.4971202694659117 |
| GB | under_5 | 50.8312_-0.0646 | Woodingdean | Woodingdean | PPL | 0.933 | PPL | 0.933 | Rottingdean | 0.3045124416380185 |

## Provenance GeoNames

- `ES` : source `https://download.geonames.org/export/dump/ES.zip`, archive SHA-256 `4f488b79a54699b3d178878103052fa89af9b3ef1e1ec0be71d0eeda76b9202c`, extract `extracts/ES.tsv`, codes `['ADM1', 'ADM2', 'CAPE', 'CLDA', 'CNYN', 'FRST', 'GRGE', 'HDLD', 'HTH', 'ISL', 'ISLS', 'LCTY', 'LK', 'LKC', 'LKN', 'LKS', 'MT', 'MTS', 'PASS', 'PK', 'PKS', 'PLAT', 'PPL', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4', 'PPLA5', 'PPLC', 'PPLF', 'PPLG', 'PPLL', 'PPLR', 'PPLS', 'PRK', 'PROM', 'RESF', 'RESN', 'RESW', 'RGN', 'RGNL', 'RSV', 'SDL', 'TUND', 'UPLD', 'VLC']`.
- `FR` : source `https://download.geonames.org/export/dump/FR.zip`, archive SHA-256 `f39c60910f77bd8dec59ed6ee27a5e2550887b2a3adb3824ba576adb84f86c3c`, extract `extracts/FR.tsv`, codes `['ADM1', 'ADM2', 'CAPE', 'CLDA', 'CNYN', 'FRST', 'GRGE', 'HDLD', 'HTH', 'ISL', 'ISLS', 'LCTY', 'LK', 'LKC', 'LKN', 'LKS', 'MT', 'MTS', 'PASS', 'PK', 'PKS', 'PLAT', 'PPL', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4', 'PPLA5', 'PPLC', 'PPLF', 'PPLG', 'PPLL', 'PPLR', 'PPLS', 'PRK', 'PROM', 'RESF', 'RESN', 'RESW', 'RGN', 'RGNL', 'RSV', 'SDL', 'TUND', 'UPLD', 'VLC']`.
- `GB` : source `https://download.geonames.org/export/dump/GB.zip`, archive SHA-256 `eaeab49c89415f5b3a11827c8922a830aadf9fed0b78076b30b5ba27bad25c70`, extract `extracts/GB.tsv`, codes `['ADM1', 'ADM2', 'CAPE', 'CLDA', 'CNYN', 'FRST', 'GRGE', 'HDLD', 'HTH', 'ISL', 'ISLS', 'LCTY', 'LK', 'LKC', 'LKN', 'LKS', 'MT', 'MTS', 'PASS', 'PK', 'PKS', 'PLAT', 'PPL', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4', 'PPLA5', 'PPLC', 'PPLF', 'PPLG', 'PPLL', 'PPLR', 'PPLS', 'PRK', 'PROM', 'RESF', 'RESN', 'RESW', 'RGN', 'RGNL', 'RSV', 'SDL', 'TUND', 'UPLD', 'VLC']`.
- `IE` : source `https://download.geonames.org/export/dump/IE.zip`, archive SHA-256 `145fe7e1d3f5d172fe0d2a77f71e41f30e4096c17644a0879d72c4c682f351d5`, extract `extracts/IE.tsv`, codes `['ADM1', 'ADM2', 'CAPE', 'CLDA', 'CNYN', 'FRST', 'GRGE', 'HDLD', 'HTH', 'ISL', 'ISLS', 'LCTY', 'LK', 'LKC', 'LKN', 'LKS', 'MT', 'MTS', 'PASS', 'PK', 'PKS', 'PLAT', 'PPL', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4', 'PPLA5', 'PPLC', 'PPLF', 'PPLG', 'PPLL', 'PPLR', 'PPLS', 'PRK', 'PROM', 'RESF', 'RESN', 'RESW', 'RGN', 'RGNL', 'RSV', 'SDL', 'TUND', 'UPLD', 'VLC']`.
