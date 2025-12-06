import pandas as pd
import numpy as np
import requests
from functools import reduce
import warnings

athletes = pd.read_csv("C:/Users/acer/Downloads/athlete_events.csv")
nocs = pd.read_csv("C:/Users/acer/Downloads/noc_regions.csv")

athletes = athletes.drop_duplicates()

medal_map = {"Gold": 1, "Silver": 3, "Bronze": 2}
athletes['Medal_score'] = athletes['Medal'].map(medal_map).fillna(0).astype(int)
athletes['Medal'] = athletes['Medal'].fillna("Inconnu")

# Nouvelle colonne: 1 si médaille, 0 sinon
athletes['has_medal'] = athletes['Medal'].apply(lambda x: 0 if x == "Inconnu" else 1)

athletes = athletes[athletes['Age'].notna()]
athletes['Age'] = athletes['Age'].astype(int)

nocs['region'] = nocs.apply(
    lambda row: row['notes'] if pd.notna(row['notes']) and row['notes'] not in ['Unknown', 'Refugee Olympic Team']
    else "Unknown" if pd.isna(row['region'])
    else row['region'],
    axis=1
)
nocs = nocs[nocs['region'].str.lower() != 'unknown']

athletes_regions = athletes.merge(
    nocs[['NOC', 'region']],
    on='NOC',
    how='left'
)

athletes_regions.loc[
    (athletes_regions['NOC'] == 'SGP') & (athletes_regions['region'].isna()),
    'region'
] = 'Singapore'

athletes_regions = athletes_regions[athletes_regions['region'].notna()]

# Création des tables de dimension
dim_date = athletes_regions[['Year']].drop_duplicates().reset_index(drop=True)
dim_date['id_date'] = dim_date.index + 1
dim_date.to_csv('dim_date.csv', index=False)

dim_event = athletes_regions[['Year', 'Sport', 'Event', 'Season']].drop_duplicates().reset_index(drop=True)
dim_event = dim_event.merge(dim_date, on='Year', how='left')
dim_event['id_event'] = dim_event.index + 1
dim_event = dim_event[['id_event', 'id_date', 'Sport', 'Event', 'Season']]
#dim_event.to_csv('dim_event.csv', index=False)

dim_region = athletes_regions[['NOC', 'region']].drop_duplicates().reset_index(drop=True)
dim_region['id_region'] = dim_region.index + 1
dim_region = dim_region[['id_region', 'NOC', 'region']]
#dim_region.to_csv('dim_region.csv', index=False)

fact_athlete = athletes_regions.copy()
fact_athlete = fact_athlete.merge(dim_event, on=['Sport', 'Event', 'Season'], how='left')
fact_athlete = fact_athlete.merge(dim_region[['NOC', 'region', 'id_region']], on=['NOC', 'region'], how='left')

fact_athlete = fact_athlete[['Name', 'Sex', 'Age', 'id_date', 'id_event', 'id_region', 'Medal', 'Medal_score', 'has_medal']]
fact_athlete.to_csv('fact_athlete.csv', index=False)

##api world banck
warnings.filterwarnings('ignore')

indicators = {
    "SP.POP.TOTL": "population",
    "NY.GDP.MKTP.CD": "gdp_total",
    "NY.GDP.PCAP.CD": "gdp_per_capita",
    "SP.URB.TOTL.IN.ZS": "urban_population",
    "SP.POP.1564.TO": "population_active",
    "NY.GDP.MKTP.KD.ZG": "gdp_growth"
}

all_dataframes = []
for code, col_name in indicators.items():
    url = f"https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&per_page=20000"
    response = requests.get(url)

    try:
        data = response.json()

        if len(data) < 2:
            print(f"Pas de données pour {col_name}")
            continue

        df = pd.json_normalize(data[1])
        df = df[['country.id', 'country.value', 'countryiso3code', 'date', 'value']]
        df.rename(columns={'value': col_name}, inplace=True)
        all_dataframes.append(df)
        print(f"{col_name}: {len(df)} lignes récupérées")

    except Exception as e:
        print(f"Erreur avec {code}: {e}")
print(" Fusion des données...")
df_final = reduce(lambda left, right: pd.merge(
    left, right,
    on=['country.id', 'country.value', 'countryiso3code', 'date'],
    how='outer'
), all_dataframes)
df_final.rename(columns={
    'country.id': 'country_code',
    'country.value': 'country_name',
    'countryiso3code': 'iso3',
    'date': 'year'
}, inplace=True)

print(f"\nDimensions initiales: {df_final.shape}")
print("\nvant nettoyage - Valeurs manquantes (%):")
print((df_final.isna().mean() * 100).sort_values(ascending=False))
print(" Nettoyage en cours...")

df_final = df_final.drop_duplicates(subset=['country_code', 'year'])
numeric_cols = list(indicators.values())
for col in numeric_cols:
    df_final[col] = pd.to_numeric(df_final[col], errors='coerce')
# Interpolation linéaire pour les variables continues
print("   → Interpolation linéaire (PIB, PIB/habitant)...")
for col in ['gdp_total', 'gdp_per_capita']:
    df_final[col] = df_final.groupby('country_code')[col].transform(
        lambda x: x.interpolate(method='linear', limit_direction='both') if len(x.dropna()) > 0 else x
    )
# Population active: remplir avec le minimum du même pays
df_final['population_active'] = df_final.groupby('country_code')['population_active'].transform(
lambda x: x.fillna(x.min()) if len(x.dropna()) > 0 else x
    )

# Remplissage par médiane pour les variables avec plus de variation
for col in ['urban_population', 'gdp_growth']:
    df_final[col] = df_final.groupby('country_code')[col].transform(
        lambda x: x.fillna(x.median()) if len(x.dropna()) > 0 else x
    )
print(f"Dimensions finales: {df_final.shape}")
print("Après nettoyage - Valeurs manquantes (%):")
missing_percent = (df_final.isna().mean() * 100).sort_values(ascending=False)
df_final['urban_population_numeric'] = (df_final['population'] * df_final['urban_population'] / 100).round(0)
before_count = len(df_final)
df_final = df_final[df_final['iso3'].notna()]
after_count = len(df_final)
# === APERÇU DES DONNÉES ===
print("Aperçu des premières lignes:")
print(df_final.head(10))

print("Exemples de codes ISO3:")
print(df_final[['country_name', 'iso3', 'country_code']].drop_duplicates().head(10))

# Optionnel: Sauvegarder
#df_final.to_csv('worldbank_data_cleaned.csv', index=False)
# print("\n💾 Données sauvegardées dans 'worldbank_data_cleaned.csv'")