# Audit prépublication — cascade GeoNames

- Pays : `FR`
- Bbox : `[-6.0, 41.0, 8.0, 51.0]`
- Spots analysés : **2189**
- Divergences runtime/audit : **0**

> **Avertissement :** Corpus contient 2189 spots, référence indicative 2139 (écart +50).

## Darkness

| Corpus | Valides | Invalides | Min | P25 | Médiane | P75 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| Global | 2189 | 0 | 0.0 | 0.546701 | 0.739238 | 0.85227 | 1.0 |
| FR | 2189 | 0 | 0.0 | 0.546701 | 0.739238 | 0.85227 | 1.0 |

### Bins fixes de darkness (0,1)

| Corpus | 0.0-0.1 | 0.1-0.2 | 0.2-0.3 | 0.3-0.4 | 0.4-0.5 | 0.5-0.6 | 0.6-0.7 | 0.7-0.8 | 0.8-0.9 | 0.9-1.0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Global | 23 | 51 | 27 | 139 | 222 | 183 | 180 | 484 | 493 | 387 |
| FR | 23 | 51 | 27 | 139 | 222 | 183 | 180 | 484 | 493 | 387 |

## Emprise ALR valide

- Raster : `/tmp/darkskyspots-france-mJlGtB/debug_darkness_france_2025.tif`
- Pixels finis : **4765367 / 11711566**
- Emprise WGS84 : `[-4.891666666666675, 42.49583333333333, 6.004166666666659, 51.99999999999999]`
- Couverture des bords : `{'west': False, 'south': False, 'east': False, 'north': True, 'all': False}`

## Distances de la cascade

| Pays | <5 km | 5–25 km | 25–40 km | ADM2 | ADM1 |
|---|---:|---:|---:|---:|---:|
| `FR` | 2182 | 7 | 0 | 0 | 0 |
| **Global** | 2182 | 7 | 0 | 0 | 0 |

## Gagnants par code

| Pays | Code | Spots |
|---|---|---:|
| `FR` | `CAPE` | 1 |
| `FR` | `FRST` | 107 |
| `FR` | `GRGE` | 1 |
| `FR` | `ISL` | 5 |
| `FR` | `LK` | 10 |
| `FR` | `MT` | 24 |
| `FR` | `MTS` | 3 |
| `FR` | `PASS` | 5 |
| `FR` | `PK` | 36 |
| `FR` | `PPL` | 1979 |
| `FR` | `PPLA2` | 1 |
| `FR` | `PPLA3` | 4 |
| `FR` | `PPLA5` | 1 |
| `FR` | `PPLL` | 5 |
| `FR` | `PRK` | 2 |
| `FR` | `RGN` | 3 |
| `FR` | `UPLD` | 2 |

## Contrôle des pays

- Codes inattendus : `{}`
- IM/JE/GG : `{'IM': 0, 'JE': 0, 'GG': 0}`

## Îles

| Île | Pays | Natural Earth 1:10m | Spots proches | Statut |
|---|---|---|---:|---|
| Isle of Wight | `GB` | oui | 0 | `covered_by_natural_earth_no_spot` |
| Anglesey | `GB` | oui | 0 | `covered_by_natural_earth_no_spot` |
| Isles of Scilly | `GB` | oui | 0 | `covered_by_natural_earth_no_spot` |
| Lundy | `GB` | oui | 0 | `covered_by_natural_earth_no_spot` |
| Isle of Sheppey | `GB` | oui | 0 | `covered_by_natural_earth_no_spot` |
| Achill Island | `IE` | oui | 0 | `covered_by_natural_earth_no_spot` |
| Valentia Island | `IE` | oui | 0 | `covered_by_natural_earth_no_spot` |
| Arranmore | `IE` | oui | 0 | `covered_by_natural_earth_no_spot` |

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
| FR | under_5 | 48.6812_-2.3188 | Cap Fréhel | Cap Fréhel | CAPE | 0.639 | CAPE | 0.639 | Fréhel | 0.9493970027058072 |
| FR | under_5 | 42.9562_0.9812 | Forêt de Bourrudech | Forêt de Bourrudech | FRST | 1.677 | FRST | 1.677 | Moulis | 0.8453497294026767 |
| FR | under_5 | 44.2771_3.2771 | Gorges du Tarn | Gorges du Tarn | GRGE | 2.249 | GRGE | 2.249 | Sainte-Enimie | 0.9897869644292336 |
| FR | 5_to_25 | 43.4604_4.5271 | Le Cassieu | Le Cassieu | ISL | 6.728 | ISL | 6.728 | Saintes-Maries-de-la-Mer | 0.5165801571204972 |
| FR | under_5 | 43.5562_4.7854 | Étang des Aulnes | Étang des Aulnes | LK | 3.886 | LK | 3.886 | Saint-Martin-de-Crau | 0.32172896695013287 |
| FR | under_5 | 43.2312_5.4687 | Mont de la Gardiole | Mont de la Gardiole | MT | 0.940 | MT | 0.940 | Vaufrège | 0.06723796860472353 |
| FR | under_5 | 43.1396_3.0854 | Montagne de la Clape | Montagne de la Clape | MTS | 2.281 | MTS | 2.281 | Gruissan | 0.3511053124668133 |
| FR | under_5 | 43.3229_-1.6813 | Col d'Ibardin | Col d'Ibardin | PASS | 0.715 | PASS | 0.715 | Urrugne | 0.3283859366912818 |
| FR | under_5 | 42.8104_0.2104 | Pic Cabanou | Pic Cabanou | PK | 2.152 | PK | 2.152 | Saint-Lary-Soulan | 0.9363553807949505 |
| FR | under_5 | 42.7937_0.0146 | Gèdre | Gèdre | PPL | 0.871 | PPL | 0.871 | Luz-Saint-Sauveur | 0.9310140832333134 |
| FR | under_5 | 46.6729_5.5562 | Lons-le-Saunier | Lons-le-Saunier | PPLA2 | 0.273 | PPLA2 | 0.273 | Lons-le-Saunier | 0.6012905985062957 |
| FR | under_5 | 44.7437_5.3771 | Die | Die | PPLA3 | 1.218 | PPLA3 | 1.218 | Die | 0.7922950417864193 |
| FR | under_5 | 43.3646_5.3354 | Marseille 16 | Marseille 16 | PPLA5 | 0.539 | PPLA5 | 0.539 | Saint-Henri | 0.0 |
| FR | under_5 | 43.9146_5.2854 | Clavaillan | Clavaillan | PPLL | 1.072 | PPLL | 1.072 | Roussillon | 0.4928371670699706 |
| FR | under_5 | 44.0979_3.5937 | Parc National des Cévennes | Parc National des Cévennes | PRK | 2.175 | PRK | 2.175 | Valleraugue | 0.842911458758196 |
| FR | under_5 | 43.5062_4.3229 | Petite Camargue | Petite Camargue | RGN | 0.858 | RGN | 0.858 | Saintes-Maries-de-la-Mer | 0.49660354248233873 |
| FR | under_5 | 43.0437_2.8604 | Pla del Pal | Pla del Pal | UPLD | 2.193 | UPLD | 2.193 | Portel-des-Corbières | 0.5238483970908423 |
| FR | under_5 | 42.8146_-0.1688 | la Huchole | la Huchole | PK | 1.186 | PK | 1.186 | Cauterets | 0.8884020084125125 |
| FR | under_5 | 42.8146_-0.5771 | Pic d'Arri | Pic d'Arri | PK | 2.583 | PK | 2.583 | Bedous | 0.8441987751638065 |
| FR | under_5 | 42.8604_0.6604 | Pic de Maupas | Pic de Maupas | PK | 0.661 | PK | 0.661 | Cierp-Gaud | 0.8144685042035005 |
| FR | under_5 | 42.8771_-0.3521 | Crête les Quintétes | Crête les Quintétes | PK | 0.827 | PK | 0.827 | Arrens-Marsous | 0.8706552543179165 |
| FR | under_5 | 42.8771_0.3979 | Bordères-Louron | Bordères-Louron | PPL | 0.599 | PPL | 0.599 | Arreau | 0.9056094282616404 |
| FR | under_5 | 42.8854_0.6146 | Guran | Guran | PPL | 0.525 | PPL | 0.525 | Cierp-Gaud | 0.8580892823744883 |
| FR | under_5 | 42.8854_0.9896 | Balacet | Balacet | PPL | 0.681 | PPL | 0.681 | Moulis | 0.874292967416868 |
| FR | under_5 | 42.8896_-0.5813 | Pène d'Udapet | Pène d'Udapet | PK | 0.662 | PK | 0.662 | Bedous | 0.8723759668739601 |
| FR | under_5 | 42.9021_1.2062 | Soueix-Rogalle | Soueix-Rogalle | PPL | 0.880 | PPL | 0.880 | Oust | 0.8474162702731692 |
| FR | under_5 | 42.9146_1.3229 | Biert | Biert | PPL | 1.874 | PPL | 1.874 | Massat | 0.8517982909711533 |
| FR | under_5 | 42.9271_1.3937 | Cap du Carmil | Cap du Carmil | PK | 1.717 | PK | 1.717 | Massat | 0.8378129286071339 |
| FR | under_5 | 42.9396_-0.0313 | Ortiac | Ortiac | PPL | 1.696 | PPL | 1.696 | Pierrefitte-Nestalas | 0.86079597158501 |
| FR | under_5 | 42.9396_1.6562 | Roc Nègre | Roc Nègre | PK | 1.290 | PK | 1.290 | Montgaillard | 0.7398801713993476 |
| FR | under_5 | 42.9479_1.7521 | Roc Marot | Roc Marot | PK | 1.331 | PK | 1.331 | Montferrier | 0.7965531118648167 |
| FR | under_5 | 42.9521_0.7937 | Razecueillé | Razecueillé | PPL | 1.851 | PPL | 1.851 | Aspet | 0.860603802503523 |
| FR | under_5 | 42.9562_-0.1188 | Pic d'Escorne-Crabe | Pic d'Escorne-Crabe | PK | 1.171 | PK | 1.171 | Pierrefitte-Nestalas | 0.8392142136459771 |
| FR | under_5 | 42.9562_-0.8563 | Pic Lakhoura | Pic Lakhoura | PK | 1.518 | PK | 1.518 | Tardets | 0.8449778223426434 |
| FR | under_5 | 42.9562_0.2437 | La Séoube | La Séoube | PPL | 0.880 | PPL | 0.880 | Campan | 0.8754888093163565 |
| FR | 5_to_25 | 43.3687_4.6937 | Salin-de-Giraud | Salin-de-Giraud | PPL | 5.885 | PPL | 5.885 | Port-Saint-Louis-du-Rhône | 0.41075056046993763 |
| FR | 5_to_25 | 43.5021_4.8771 | Ventillon | Ventillon | PPL | 6.244 | PPL | 6.244 | Fos-sur-Mer | 0.18349144369857306 |
| FR | 5_to_25 | 44.0521_-0.7646 | Luglon | Luglon | PPL | 5.035 | PPL | 5.035 | Arengosse | 0.8590944113361821 |
| FR | 5_to_25 | 44.0521_-0.8104 | Le Platiet | Le Platiet | PPL | 5.412 | PPL | 5.412 | Arengosse | 0.8467289102841877 |
| FR | 5_to_25 | 44.3979_-0.9396 | La Gare de Lugos | La Gare de Lugos | PPL | 6.206 | PPL | 6.206 | Ychoux | 0.7891764697667281 |
| FR | 5_to_25 | 44.6521_-0.6813 | Saucats | Saucats | PPL | 6.700 | PPL | 6.700 | Saucats | 0.46060058851512076 |
| FR | under_5 | 42.9646_1.9646 | Sainte-Colombe-sur-l'Hers | Sainte-Colombe-sur-l'Hers | PPL | 1.372 | PPL | 1.372 | Chalabre | 0.8203971797338394 |
| FR | under_5 | 42.9687_-0.7646 | Soum de Lèche | Soum de Lèche | PK | 0.673 | PK | 0.673 | Bedous | 0.8585461212687605 |
| FR | under_5 | 42.9812_2.1729 | Bouriège | Bouriège | PPL | 0.762 | PPL | 0.762 | Espéraza | 0.7680134038914245 |
| FR | under_5 | 42.9937_2.3312 | Pla d'al Bouich | Pla d'al Bouich | PK | 0.979 | PK | 0.979 | Alet-les-Bains | 0.7396137429799898 |
| FR | under_5 | 42.9979_-1.0396 | Pic de Bizkarze | Pic de Bizkarze | PK | 0.760 | PK | 0.760 | Tardets | 0.7851304242932593 |
| FR | under_5 | 43.0021_-0.4354 | Louvie-Soubiron | Louvie-Soubiron | PPL | 1.502 | PPL | 1.502 | Laruns | 0.8406875710790083 |
| FR | under_5 | 43.0021_-0.6688 | le Layens | le Layens | PK | 2.728 | PK | 2.728 | Bedous | 0.8439766150314638 |
| FR | under_5 | 43.0021_0.2229 | le Haboura | le Haboura | PK | 0.513 | PK | 0.513 | Campan | 0.8456629522639485 |
| FR | under_5 | 43.0021_0.5396 | Escalère de Coume Nère | Escalère de Coume Nère | PK | 0.236 | PK | 0.236 | Loures-Barousse | 0.8479068509612793 |
| FR | under_5 | 43.0021_0.7604 | Arbon | Arbon | PPL | 1.176 | PPL | 1.176 | Aspet | 0.8336294012895626 |
| FR | under_5 | 43.0396_2.9521 | Hameau du Lac | Hameau du Lac | PPL | 2.059 | PPL | 2.059 | Sigean | 0.46215537133862983 |
| FR | under_5 | 43.0437_-1.3604 | Forêt de Hayra | Forêt de Hayra | FRST | 0.903 | FRST | 0.903 | Saint-Étienne-de-Baïgorry | 0.6097334526384551 |
| FR | under_5 | 43.0479_-0.1188 | la Serre | la Serre | PK | 0.528 | PK | 0.528 | Argelès-Gazost | 0.7385440103721332 |
| FR | under_5 | 43.0479_-0.2604 | Pic du Monbula | Pic du Monbula | PK | 1.648 | PK | 1.648 | Arthez-d'Asson | 0.7794037534319774 |
| FR | under_5 | 43.0479_0.0479 | Pic de la Clique | Pic de la Clique | PK | 0.393 | PK | 0.393 | Pouzac | 0.7674762142663633 |
| FR | under_5 | 43.0479_0.3812 | Lortet | Lortet | PPL | 0.564 | PPL | 0.564 | La Barthe-de-Neste | 0.731862831482829 |
| FR | under_5 | 43.0479_0.7062 | Régades | Régades | PPL | 1.311 | PPL | 1.311 | Labarthe-Rivière | 0.7364582822545077 |
| FR | under_5 | 43.0479_1.2187 | Contrazy | Contrazy | PPL | 1.384 | PPL | 1.384 | Montjoie-en-Couserans | 0.801817293684328 |
| FR | under_5 | 43.0479_2.5104 | Serre de la Pène | Serre de la Pène | PK | 1.243 | PK | 1.243 | Montlaur | 0.6935867805974161 |
| FR | under_5 | 43.0896_2.1729 | Lauraguel | Lauraguel | PPL | 0.954 | PPL | 0.954 | Pieusse | 0.6943894691072171 |
| FR | under_5 | 43.0937_-0.3938 | Sainte-Colome | Sainte-Colome | PPL | 1.241 | PPL | 1.241 | Louvie-Juzon | 0.7388270056325307 |
| FR | under_5 | 43.0937_-0.8813 | Sunhar | Sunhar | PPL | 0.251 | PPL | 0.251 | Tardets | 0.8243064919094878 |
| FR | under_5 | 43.0937_0.0604 | Astugue | Astugue | PPL | 0.959 | PPL | 0.959 | Trébons | 0.7114190204092956 |
| FR | under_5 | 43.0937_1.0146 | Betchat | Betchat | PPL | 0.318 | PPL | 0.318 | Cassagne | 0.7694868074793193 |
| FR | under_5 | 43.0937_1.4021 | Sabarat | Sabarat | PPL | 1.274 | PPL | 1.274 | Les Bordes-sur-Arize | 0.7595986576407947 |
| FR | under_5 | 43.0937_1.4854 | Monesple | Monesple | PPL | 0.755 | PPL | 0.755 | Artigat | 0.7330571645112764 |
| FR | under_5 | 43.0937_1.8937 | Mirepoix | Mirepoix | PPL | 1.736 | PPL | 1.736 | Mirepoix | 0.7781576632360082 |
| FR | under_5 | 43.0979_-1.1771 | Esterenguibel | Esterenguibel | PPL | 0.951 | PPL | 0.951 | Saint-Jean-le-Vieux | 0.7298741010329746 |
| FR | under_5 | 43.1187_2.6771 | le Crès | le Crès | PK | 2.386 | PK | 2.386 | Fabrezan | 0.6153046583996534 |
| FR | under_5 | 43.1396_0.2437 | Chelle-Spou | Chelle-Spou | PPL | 0.382 | PPL | 0.382 | Cieutat | 0.7556666792671407 |
| FR | under_5 | 43.1396_1.8021 | Bois de la Belène | Bois de la Belène | FRST | 0.717 | FRST | 0.717 | Belpech | 0.739097782470586 |
| FR | under_5 | 43.1396_2.0812 | Mazerolles-du-Razès | Mazerolles-du-Razès | PPL | 0.795 | PPL | 0.795 | Belvèze-du-Razès | 0.7426847630421859 |
| FR | under_5 | 43.1396_2.8146 | Boutenac | Boutenac | PPL | 2.105 | PPL | 2.105 | Boutenac | 0.49106118037845936 |
| FR | under_5 | 43.1437_-1.1313 | Mendive | Mendive | PPL | 1.297 | PPL | 1.297 | Saint-Jean-le-Vieux | 0.7541720897290176 |
| FR | under_5 | 43.1854_-0.1188 | Pontacq | Pontacq | PPL | 0.335 | PPL | 0.335 | Pontacq | 0.6578662442756674 |
| FR | under_5 | 43.1854_-0.7146 | Esquiule | Esquiule | PPL | 1.109 | PPL | 1.109 | Esquiule | 0.7312845408213222 |
| FR | under_5 | 43.1854_1.1187 | Saint-Christaud | Saint-Christaud | PPL | 0.933 | PPL | 0.933 | Cazères | 0.7213277578404516 |
| FR | under_5 | 43.1854_2.0271 | Fanjeaux | Fanjeaux | PPL | 0.567 | PPL | 0.567 | Fanjeaux | 0.7348808534005665 |
| FR | under_5 | 43.1896_5.6521 | Le Liouquet | Le Liouquet | PPL | 1.376 | PPL | 1.376 | Ceyreste | 0.18956159459478528 |
| FR | under_5 | 43.2271_0.4271 | Sabarros | Sabarros | PPL | 1.375 | PPL | 1.375 | Galan | 0.8310280442590028 |
| FR | under_5 | 43.2271_0.4896 | Gaussan | Gaussan | PPL | 0.255 | PPL | 0.255 | Monléon-Magnoac | 0.8556262844342555 |
| FR | under_5 | 43.2271_2.9521 | Moussan | Moussan | PPL | 0.548 | PPL | 0.548 | Moussan | 0.31753367253119524 |
| FR | under_5 | 43.2312_-0.3063 | Baliros | Baliros | PPL | 0.397 | PPL | 0.397 | Bordes | 0.515323733468305 |
| FR | under_5 | 43.2312_-0.5229 | Bois du Laring | Bois du Laring | FRST | 2.249 | FRST | 2.249 | Lasseube | 0.5945984135612666 |
| FR | under_5 | 43.2312_1.3021 | Bax | Bax | PPL | 1.250 | PPL | 1.250 | Lézat-sur-Lèze | 0.6563343138888256 |
| FR | under_5 | 43.2312_1.6646 | Mestribès | Mestribès | PPL | 2.433 | PPL | 2.433 | Mazères | 0.6341545363649526 |
| FR | under_5 | 43.2354_-0.9854 | Pagolle | Pagolle | PPL | 1.269 | PPL | 1.269 | Ordiarp | 0.7831672640014435 |
| FR | under_5 | 43.2354_-1.2688 | Ahaice | Ahaice | PPL | 0.315 | PPL | 0.315 | Ossès | 0.680661471871969 |
| FR | under_5 | 43.2521_0.6146 | Gensac-de-Boulogne | Gensac-de-Boulogne | PPL | 2.320 | PPL | 2.320 | Blajan | 0.8459381421582387 |
| FR | under_5 | 43.2604_3.1771 | Lespignan | Lespignan | PPL | 1.528 | PPL | 1.528 | Lespignan | 0.404867994882797 |
| FR | under_5 | 43.2646_0.1979 | Marquerie | Marquerie | PPL | 0.608 | PPL | 0.608 | Pouyastruc | 0.7303420383429007 |
| FR | under_5 | 43.2729_2.2646 | Ventenac-Cabardès | Ventenac-Cabardès | PPL | 1.781 | PPL | 1.781 | Ventenac-Cabardès | 0.6506002806224532 |
| FR | under_5 | 43.2729_2.5562 | Peyriac-Minervois | Peyriac-Minervois | PPL | 2.148 | PPL | 2.148 | Peyriac-Minervois | 0.6435429225847944 |
| FR | under_5 | 43.2729_2.7687 | Oupia | Oupia | PPL | 1.911 | PPL | 1.911 | Olonzac | 0.5808505453966257 |
| FR | under_5 | 43.2729_2.9062 | Le Somail | Le Somail | PPL | 0.892 | PPL | 0.892 | Mirepeisset | 0.4745292723781367 |
| FR | under_5 | 43.2729_5.7437 | Le Camp du Castellet | Le Camp du Castellet | PPL | 1.780 | PPL | 1.780 | Cuges-les-Pins | 0.25307865766371174 |
| FR | under_5 | 43.2771_-0.4854 | Aubertin | Aubertin | PPL | 0.390 | PPL | 0.390 | Aubertin | 0.4973096603658751 |
| FR | under_5 | 43.2771_-1.4521 | Pic d'Ourrezti | Pic d'Ourrezti | PK | 2.443 | PK | 2.443 | Ainhoa | 0.5467008778169469 |
| FR | under_5 | 43.2771_0.7979 | Lilhac | Lilhac | PPL | 1.350 | PPL | 1.350 | Aurignac | 0.8166056850802945 |

## Provenance GeoNames

- `ES` : source `https://download.geonames.org/export/dump/ES.zip`, archive SHA-256 `4f488b79a54699b3d178878103052fa89af9b3ef1e1ec0be71d0eeda76b9202c`, extract `extracts/ES.tsv`, codes `['ADM1', 'ADM2', 'CAPE', 'CLDA', 'CNYN', 'FRST', 'GRGE', 'HDLD', 'HTH', 'ISL', 'ISLS', 'LCTY', 'LK', 'LKC', 'LKN', 'LKS', 'MT', 'MTS', 'PASS', 'PK', 'PKS', 'PLAT', 'PPL', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4', 'PPLA5', 'PPLC', 'PPLF', 'PPLG', 'PPLL', 'PPLR', 'PPLS', 'PRK', 'PROM', 'RESF', 'RESN', 'RESW', 'RGN', 'RGNL', 'RSV', 'SDL', 'TUND', 'UPLD', 'VLC']`.
- `FR` : source `https://download.geonames.org/export/dump/FR.zip`, archive SHA-256 `f39c60910f77bd8dec59ed6ee27a5e2550887b2a3adb3824ba576adb84f86c3c`, extract `extracts/FR.tsv`, codes `['ADM1', 'ADM2', 'CAPE', 'CLDA', 'CNYN', 'FRST', 'GRGE', 'HDLD', 'HTH', 'ISL', 'ISLS', 'LCTY', 'LK', 'LKC', 'LKN', 'LKS', 'MT', 'MTS', 'PASS', 'PK', 'PKS', 'PLAT', 'PPL', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4', 'PPLA5', 'PPLC', 'PPLF', 'PPLG', 'PPLL', 'PPLR', 'PPLS', 'PRK', 'PROM', 'RESF', 'RESN', 'RESW', 'RGN', 'RGNL', 'RSV', 'SDL', 'TUND', 'UPLD', 'VLC']`.
- `GB` : source `https://download.geonames.org/export/dump/GB.zip`, archive SHA-256 `eaeab49c89415f5b3a11827c8922a830aadf9fed0b78076b30b5ba27bad25c70`, extract `extracts/GB.tsv`, codes `['ADM1', 'ADM2', 'CAPE', 'CLDA', 'CNYN', 'FRST', 'GRGE', 'HDLD', 'HTH', 'ISL', 'ISLS', 'LCTY', 'LK', 'LKC', 'LKN', 'LKS', 'MT', 'MTS', 'PASS', 'PK', 'PKS', 'PLAT', 'PPL', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4', 'PPLA5', 'PPLC', 'PPLF', 'PPLG', 'PPLL', 'PPLR', 'PPLS', 'PRK', 'PROM', 'RESF', 'RESN', 'RESW', 'RGN', 'RGNL', 'RSV', 'SDL', 'TUND', 'UPLD', 'VLC']`.
- `IE` : source `https://download.geonames.org/export/dump/IE.zip`, archive SHA-256 `145fe7e1d3f5d172fe0d2a77f71e41f30e4096c17644a0879d72c4c682f351d5`, extract `extracts/IE.tsv`, codes `['ADM1', 'ADM2', 'CAPE', 'CLDA', 'CNYN', 'FRST', 'GRGE', 'HDLD', 'HTH', 'ISL', 'ISLS', 'LCTY', 'LK', 'LKC', 'LKN', 'LKS', 'MT', 'MTS', 'PASS', 'PK', 'PKS', 'PLAT', 'PPL', 'PPLA', 'PPLA2', 'PPLA3', 'PPLA4', 'PPLA5', 'PPLC', 'PPLF', 'PPLG', 'PPLL', 'PPLR', 'PPLS', 'PRK', 'PROM', 'RESF', 'RESN', 'RESW', 'RGN', 'RGNL', 'RSV', 'SDL', 'TUND', 'UPLD', 'VLC']`.
