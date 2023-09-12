import re
from datetime import datetime, timedelta
import argparse
import threading

import Levenshtein as lev
import pandas as pd
import tabula as tb

from logs import log_init, debug_status, log, Status


# Constants
DEBUG_DF_LEN = 50
NUM_KEYWORDS_THES = 2
CLIPPING_UNITS = 5

COL_NAMES = ["Date", "Street", "On Time", "Off Time", "Duration"]
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
STREETS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15']


# Tweak_area helper Functions
def extract_pdf(path: str, adjusted_area: list) -> pd.DataFrame:
    	return pd.concat(tb.read_pdf(path,
                                     pages="all",
                                     area=adjusted_area,
                                     pandas_options={"header": None},
                                     lattice=True,
                                     multiple_tables=True),
                    		     ignore_index=True)

def find_keyword(keyword: str, df: pd.DataFrame, expand_right: bool) -> bool:
	how_many = 0
	first_column = df.iloc[:, 0]

	if ";" in keyword:
		seperated = keyword.split(";")
		keyword = seperated[0]

	if not expand_right:
		first_column = df.iloc[:, -1]
		if ";" in keyword: keyword = seperated[1]

	for row in first_column:
		if re.search(rf'\b{keyword}\b', str(row), re.IGNORECASE):
			how_many += 1
	
	if how_many >= NUM_KEYWORDS_THES: 
		return True
	return False

# This function begins by extracting data from the right side with a ball park area (bpa), It then refines this area of interest (AOI) through   
# iterations while checking each column for specific keywords. If it finds two or more keywords in a single column, it starts shrinking the area 
# from the left side. This iterative process continues until it obtains a single-column extraction with the best-defined area of interest.
#	  
#	|  bpa --> AOI <-- bpa  |
#
def tweak_area(df: pd.DataFrame, path: str, keyword: str, area: list, expected_columns: int, expand_right: bool) -> pd.DataFrame:

	if find_keyword(keyword, df,expand_right ) and len(df.columns) == expected_columns:
		return df

	if expand_right: 
		if find_keyword(keyword, df, expand_right):
			expand_right = False

		df = extract_pdf(path, area)
		area[1] += CLIPPING_UNITS #--------> AOI
		log.debug(f"{keyword} - expand right - {area}")
		return tweak_area(df, path, keyword, area, expected_columns, expand_right)

	df = extract_pdf(path, area)
	area[3] -= CLIPPING_UNITS # AOI <--------
	log.debug(f"{keyword} - expand left - {area}")
	return tweak_area(df, path, keyword, area, expected_columns, expand_right)

# This class provides methods for cleaning and formatting data in a DataFrame. It is designed to handle data with specific formatting issues such as 
# noise, carriage returns, double characters, and malformed dates, streets, and timings. The methods either remove the irrelevant data or mark malformed 
# data for further processing. 
class CleanData:
	def __init__(self):
		pass

	def _remove_noise(self, elem: str) -> str:
		elem = str(elem).lower().replace(";", ":").replace(",", ";").replace("hours", "").replace("hour", "").replace("am", "AM").replace("pm", "PM")
		# all characters except '/r', alphabets, numbers, colons and semi-colons are removed
		elem = re.sub(r'[^a-zA-Z0-9:;.\s]|(?!/r)', '', str(elem)).strip()
		return elem

	def _fix_carriage_return(self, df: pd.DataFrame) -> pd.DataFrame:
		num_of_cols = df.shape[1]

		for col in range(num_of_cols):
			df[col] = df[col].str.split('\r')
			df = df.explode(col)

		df.reset_index(drop=True, inplace=True)
		return df

	def _fix_double_chars(self, elem: str) -> str:
		elem = re.sub(r"\s+", "", elem)
		elem = re.sub(r";+", ";", elem)
		return elem

	def _add_alignment(self, elem: str) -> str:
		col_names_lower = [col.lower().replace(" ", "") for col in COL_NAMES]
		if elem in col_names_lower:
			return "AP" # alignment point 
		return elem

	#########  MIGHT NEED MORE WORK  ######### 
	def _checking_malformed_date(self, date: str) -> str:
		if re.match(r'^\d{2};[a-zA-Z]+;\d{4}$', date):  
			split_date = date.split(';')
			
			corrected_month = min(MONTHS, key=lambda x: lev.distance(x.lower(), split_date[1]))
			numerical_month = MONTHS.index(corrected_month.title()) + 1 # cuz list index starts from 0
			
			split_date[1] = str(numerical_month)
			return "-".join(split_date)	
		return "marker" # leaving marker where date is malformed

	def _checking_malformed_street(self, street: str) -> str:
		if street == "AP":
			return street

		elif street in STREETS:
			return street 
		return None

	def _checking_malformed_timing(self, time: str) -> str:
		time = time.strip(";")
		
		if time == "AP" or re.match(r'^\d{1}$|^\d{1}\.\d{1}$', time):
			return time
		
		elif re.match(r'^(0?[1-9]|1[0-2]):[0-5][0-9][apAP][mM]$', time):
			split_time = time[:-2]  
			am_pm = time[-2:]
			return f"{split_time};{am_pm}"
		return None
	 
	def clean_date(self, date: pd.DataFrame) -> pd.DataFrame:
		date = date.map(self._remove_noise)
		date = self._fix_carriage_return(date)
		date = date.map(self._fix_double_chars)

		date = date[date[0] != "Date".lower()]
		date = date.reset_index(drop=True)

		date = date.map(lambda x: x.split(";", 1)[1].rstrip(';'))
		date = date.map(self._checking_malformed_date)
		date.rename(columns={0 : "Date"}, inplace=True)
		return date

	def clean_street(self, street: pd.DataFrame) -> pd.DataFrame:
		street = street.map(self._remove_noise)
		street = self._fix_carriage_return(street)
		street = street.map(self._fix_double_chars)
		street = street.map(self._add_alignment)

		street = street.map(self._checking_malformed_street)
		street = street.dropna()
		street = street.reset_index(drop=True)
		street = street.rename(columns={0 : "Street"})
		return street

	def clean_time(self, time: pd.DataFrame) -> pd.DataFrame:
		time = time.map(self._remove_noise)
		time = self._fix_carriage_return(time)
		time = time.map(self._fix_double_chars)
		time = time.map(self._add_alignment)
		
		time = time.map(self._checking_malformed_timing)
		time = time.dropna()
		time = time.reset_index(drop=True)
		time = time.rename(columns={0 : "On Time", 1 : "Off Time", 2 : "Duration"})
		return time

def get_first_date(date: pd.DataFrame) -> str:
	FIRST_ROW = 0 
	search_value = "marker"
	valid_date_indices = []
	marker_indices = []

	if date.at[FIRST_ROW, "Date"] != search_value:
		log.debug(f"Date - 1st date is not a marker - {date.at[FIRST_ROW, 'Date']}")
		return date.at[FIRST_ROW, "Date"]
	
	marker_indices = date.index[date["Date"] == search_value].tolist()
	valid_date_indices = date.index[date["Date"] != search_value].tolist()

	for _ in marker_indices:
		if _ != len(date) - 1: # dont care about the last date
			next_valid_idx = min(list(filter(lambda x: x > _, valid_date_indices)))
			valid_date = datetime.strptime(date.at[next_valid_idx, "Date"], '%d-%m-%Y')

			corrected = valid_date - timedelta(days=next_valid_idx - _)

			date.at[_, "Date"] = corrected.strftime('%d-%m-%Y')

	log.debug(f"Date - 1st date after substituting marker - {date.at[FIRST_ROW, 'Date']}")
	return date.at[FIRST_ROW, "Date"]

def convert_to_24_hours(time_str: str) -> str:
	if re.match(r'\d{1,2}:\d{2};[APap][Mm]', time_str):
		time_12h = datetime.strptime(time_str, '%I:%M;%p')
		time_24h = time_12h.strftime('%H:%M')
		return time_24h
	return time_str

def add_structured_dates(street: pd.DataFrame, corr_date: datetime) -> pd.DataFrame:
	date = datetime.strptime(corr_date, '%d-%m-%Y')

	# spliting the STREETS list into two groups.
	mask_group_one = street.isin(STREETS[:7]).any(axis=1)
	mask_group_two = street.isin(STREETS[7:]).any(axis=1)

	day_increment = timedelta(days=1)
	generated_dates = []
	incre_date = False

	for _ in range(len(street)):
		if mask_group_one[_]:
			if incre_date:
				date += day_increment
				incre_date = False
			generated_dates.append(date.strftime('%d-%m-%Y'))

		elif mask_group_two[_]:
			if not incre_date:
				date += day_increment
				incre_date = True
			generated_dates.append(date.strftime('%d-%m-%Y'))

	date_df = pd.DataFrame({"Date": generated_dates})
	return date_df

def merge_from_alignment(date: pd.DataFrame, street: pd.DataFrame, time: pd.DataFrame) -> pd.DataFrame:
	search_value = 'AP'
	temp_1 = []
	temp_2 = []

	strt_indices = street.index[street["Street"] == search_value].tolist()
	time_indices = time.index[time["On Time"] == search_value].tolist()

	# stopping points
	strt_indices.append(len(street))
	time_indices.append(len(time))

	for _ in range(len(strt_indices) - 1):
		street_sect = street.iloc[strt_indices[_] + 1:strt_indices[_ + 1]]
		temp_1.append(street_sect)

		time_sect = time.iloc[time_indices[_] + 1:time_indices[_ + 1]]
		temp_2.append(time_sect)

	merged_df = pd.merge(pd.concat(temp_1, axis=0), pd.concat(temp_2, axis=0), left_index=True, right_index=True, how='outer')
	merged_df = merged_df.reset_index(drop=True)

	merged_df = pd.concat([date, merged_df], axis=1)
	return merged_df

class VerifyData:
	def __init__(self):
		pass

	def _veri_streets(self, street_sect: pd.DataFrame) -> bool:
		veri_strt = 0

		for _ in street_sect:
			if _ in STREETS:
				veri_strt += 1
		
		if veri_strt == len(street_sect):
			return True
		return False

	def _veri_dates(self, dates_sect: pd.DataFrame) -> bool:
		FIRST_ROW = 0
		SECOND_ROW = 1
		
		prev_date = datetime.strptime(dates_sect.iat[FIRST_ROW], '%d-%m-%Y')

		for _ in range(SECOND_ROW, len(dates_sect)):
			curr_date = datetime.strptime(dates_sect.iat[_], '%d-%m-%Y')
			diff = curr_date - prev_date # using the bigger minus smaller date to measure diff of 1

			if diff == timedelta(days=0): # avoid false trigger on date stretching
				continue
			
			if diff != timedelta(days=1):
				return False
			
			prev_date = curr_date
		return True

	def analyse(self, combined: pd.DataFrame) -> pd.DataFrame:
		if not self._veri_streets(combined["Street"]):
			log.critical("Street - Street elements do not match predefined street numbers")

		self._veri_dates(combined["Date"])
			# log.critical("Date - Dates are not incremented by one.")
def main():		
	# Debug format: LINE NUMBER - DATAFRAME - DOING WHAT? - ANY VALUE CHANGES IN PROGRESS OR PASS/FAIL
	log_init(log.DEBUG)

	parser = argparse.ArgumentParser(description='Extracts data from water schedule')
	parser.add_argument('-t', type=int, help='1-6: Select the pdf to test')
	args = parser.parse_args()

	test_num = args.t

	# Ball park areas and column sizes for each dataframe
	date_area_col   = ([40, 100, 920, 300], 1)
	street_area_col = ([40, 220, 920, 360], 1)
	timing_area_col = ([40, 275, 920, 830], 3)

	path = f"samples/test{test_num}.pdf"	
	unprocs = {}
	log.debug(f"Processing data of {path[8:]}: ")

	try: 
		date_thread = threading.Thread(target=lambda: unprocs.update({"date": tweak_area(extract_pdf(path, 
													     date_area_col[0]), 
													     path, 
													     "Date", 
													     date_area_col[0], 
													     date_area_col[1], 
													     expand_right=True)}))
		
		street_thread = threading.Thread(target=lambda: unprocs.update({"street": tweak_area(extract_pdf(path, 
														 street_area_col[0]), 
														 path, 
														 "Street", 
														 street_area_col[0], 
														 street_area_col[1], 
														 expand_right=True)}))
		
		timing_thread = threading.Thread(target=lambda: unprocs.update({"time": tweak_area(extract_pdf(path, 
														 timing_area_col[0]), 
														 path, 
														 "On Time;Duration", 
														 timing_area_col[0], 
														 timing_area_col[1], 
														 expand_right=True)}))
		
		date_thread.start()
		street_thread.start()
		timing_thread.start()

		threads = [date_thread, street_thread, timing_thread]

		for thread in threads:
			thread.join()

	except:
		status = Status.FAIL
	else:
		status = Status.PASS
	debug_status("ALL - Tweaking area to extract correct data", status)

	try:
		Cleaning  = CleanData()
		date_clean  = Cleaning.clean_date(unprocs["date"])
		street_clean = Cleaning.clean_street(unprocs["street"])
		time_clean = Cleaning.clean_time(unprocs["time"])

	except:
		status = Status.FAIL

	else:
		status = Status.PASS
	debug_status("ALL - Cleaning and formatting data", status)

	try:
		date_corrected = get_first_date(date_clean)
		time_converted = time_clean.map(convert_to_24_hours)	
		generated_dates = add_structured_dates(street_clean, date_corrected)
	
	except:
		status = Status.FAIL
	else:
		status = Status.PASS
	debug_status("ALL - Process date and time data for structured output using street and date information", status)

	try:
		combined = merge_from_alignment(generated_dates, street_clean, time_converted)
		Verifying = VerifyData()
		Verifying.analyse(combined)
		print(combined.head(DEBUG_DF_LEN))

	except ValueError as ve:
		print(ve)
		status = Status.FAIL
	else:
		status = Status.PASS
	debug_status("ALL - Verifying every element of df via their required methods", status)
	

if __name__ == "__main__":
	main()
