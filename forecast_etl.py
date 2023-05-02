import requests
import os
import pandas as pd
import json
import psycopg2
from datetime import datetime
from matplotlib import pyplot as plt
from dotenv import load_dotenv


class ForecastETL:
    def __init__(self):
        self.city_query = "stockholm,se"
        self.response = None
        self.raw_data = None
        self.normalized_dataframe = None
        self.harmonized_dataframe = None

    @staticmethod
    def get_api_key():
        load_dotenv()
        openweathermap_api_key = os.getenv('OPENWEATHERMAP_API_KEY')
        return openweathermap_api_key

    def extract_forecast(self):
        try:
            url = f'http://api.openweathermap.org/data/2.5/forecast?q=' \
                  f'{self.city_query}&appid={self.get_api_key()}&units=metric'

            response = requests.get(url)
            response.raise_for_status()
            self.response = response.json()
            self.raw_data = self.response
            return self.raw_data
        except requests.exceptions.RequestException as e:
            print(f"Error during API request: {e}")
            return None
        except Exception as e:
            print(f"Error during extracting forecast: {e}")
            return None

    def transform_forecast(self):
        if not self.raw_data:
            self.extract_forecast()

        data = {}
        for i in range(0, len(self.response["list"])):
            fetch_date = datetime.now().strftime("%Y-%m-%d")
            date_time = self.response["list"][i]["dt_txt"]
            date, time = date_time.split(' ')
            location = self.response["city"]["name"]
            temperature = self.response["list"][i]["main"]["temp"]
            air_pressure = self.response["list"][i]["main"]["pressure"]
            weather_description = self.response["list"][i]["weather"][0]["description"]
            precipitation = self.response["list"][i]["pop"]
            data[i] = {'Fetch Date': fetch_date, 'Date': date, 'Time': time, 'Location': location,
                       'Temperature': temperature,
                       'Air Pressure': air_pressure,
                       'Weather Description': weather_description, 'Precipitation': precipitation}

        self.normalized_dataframe = pd.json_normalize(self.raw_data['list'])
        self.harmonized_dataframe = pd.DataFrame.from_dict(
            data, orient='index')
        return self.normalized_dataframe, self.harmonized_dataframe

    def save_files(self):
        try:
            if not os.path.exists("Docs"):
                os.mkdir("Docs")

            with open('Docs/raw_data_json.json', 'w') as outfile:
                json.dump(self.raw_data, outfile)

            self.normalized_dataframe.to_json(
                'Docs/normalized_dataframe_json.json')
            self.harmonized_dataframe.to_csv(
                'Docs/harmonized_forecast.csv', index=False)
        except Exception as e:
            print(f"Error during saving files: {e}")

    def init_db(self):
        try:
            load_dotenv()
            db_password = os.getenv('DB_PASSWORD')

            # Connect to the database if it exists, otherwise create a new one
            connection = psycopg2.connect(
                host="localhost",
                database="postgres",
                user="postgres",
                password=db_password,
                port=5432)

            # Create a cursor
            cursor = connection.cursor()

            # Create the date dimension table
            cursor.execute("DROP TABLE IF EXISTS dim_date CASCADE")
            cursor.execute("CREATE TABLE dim_date "
                        "(year INTEGER, "
                        "month INTEGER, "
                        "day INTEGER, "
                        "full_date DATE PRIMARY KEY)")

            # Create the time dimension table
            cursor.execute("DROP TABLE IF EXISTS dim_time CASCADE")
            cursor.execute("CREATE TABLE dim_time "
                        "(hour INTEGER, "
                        "minute INTEGER, "
                        "full_time TIME PRIMARY KEY)")

            # Create the location dimension table
            cursor.execute("DROP TABLE IF EXISTS dim_location CASCADE")
            cursor.execute("CREATE TABLE dim_location "
                        "(latitude REAL, "
                        "longitude REAL, "
                        "location_city TEXT PRIMARY KEY,"
                        "location_country TEXT)")

            # Create the weather table with foreign keys to the dimension tables
            cursor.execute("DROP TABLE IF EXISTS weather CASCADE")
            cursor.execute("CREATE TABLE weather "
                        "(fetch_date TEXT, "
                        "date_id DATE REFERENCES dim_date(full_date), "
                        "time_id TIME REFERENCES dim_time(full_time), "
                        "location_id TEXT REFERENCES dim_location(location_city), "
                        "temperature REAL, "
                        "air_pressure REAL, "
                        "weather_description TEXT, "
                        "precipitation REAL)")

            # Load the data into the table
            self.load_db(connection, cursor)

            # Close the cursor and the connection
            cursor.close()
            connection.close()

        except psycopg2.Error as e:
            print(f"Error during database connection or operation: {e}")
        except Exception as e:
            print(f"Error during initializing database: {e}")
    
    def load_db(self, connection, cursor):
        # Insert the data into the dimension tables and retrieve the primary keys
        cursor.execute("INSERT INTO dim_location (location_city, location_country, latitude, longitude) VALUES (%s, %s, %s, %s) "
                       "ON CONFLICT (location_city) DO NOTHING RETURNING location_city",
                       (self.response["city"]["name"], self.response["city"]["country"], self.response["city"]["coord"]["lat"],
                        self.response["city"]["coord"]["lon"]))
        location_result = cursor.fetchone()

        if location_result:
            location_name = location_result[0]
        else:
            location_name = None

        for i in range(0, len(self.response["list"])):
            date_time = self.response["list"][i]["dt_txt"]
            date, time = date_time.split(' ')

            cursor.execute("INSERT INTO dim_date (year, month, day, full_date) VALUES (%s, %s, %s, %s) "
                           "ON CONFLICT (full_date) DO UPDATE SET year = excluded.year, month = excluded.month, day = excluded.day RETURNING full_date",
                           (int(date[:4]), int(date[5:7]), int(date[8:]), date))
            full_date_result = cursor.fetchone()

            if full_date_result:
                full_date = full_date_result[0]
            else:
                full_date = None

            cursor.execute("INSERT INTO dim_time (hour, minute, full_time) VALUES (%s, %s, %s) "
                           "ON CONFLICT (full_time) DO UPDATE SET hour = excluded.hour, minute = excluded.minute RETURNING full_time",
                           (int(time[:2]), int(time[3:5]), time))
            full_time_result = cursor.fetchone()

            if full_time_result:
                full_time = full_time_result[0]
            else:
                full_time = None

            # Insert the data into the weather table using the primary keys from the dimension tables
            cursor.execute(
                "INSERT INTO weather (fetch_date, date_id, time_id, location_id, temperature, air_pressure, weather_description, precipitation) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (datetime.now().strftime("%Y-%m-%d"), full_date, full_time, location_name,
                 self.response["list"][i]["main"]["temp"], self.response["list"][i]["main"]["pressure"],
                 self.response["list"][i]["weather"][0]["description"], self.response["list"][i]["pop"]))

        # Commit the changes
        connection.commit()

    def plot_forecast(self):
        fig, ax = plt.subplots(figsize=(10, 6))

        # Customize the plot
        ax.plot(self.harmonized_dataframe['Time'], self.harmonized_dataframe['Temperature'],
                marker='o', linestyle='solid', linewidth=2, markersize=8, label='Temperature')
        ax.set_title(f"Temperatures in {self.city_query}", fontsize=20, fontweight='bold')
        ax.set_xlabel('Time', fontsize=16)
        ax.set_ylabel('Temperature (°C)', fontsize=16)
        ax.set_xticks(self.harmonized_dataframe['Time'][::2])
        ax.set_xticklabels(self.harmonized_dataframe['Time'][::2], rotation=45, fontsize=12)
        ax.legend(fontsize=14)

        # Display the plot
        # plt.tight_layout()
        plt.show()

