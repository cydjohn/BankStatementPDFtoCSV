import os
import argparse
import re
import csv
from datetime import datetime as dt

from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LTPage, LTChar, LTAnno, LAParams, LTTextBox, LTTextLine
from pdfminer.pdfpage import PDFPage


CSV_DATE_FORMAT = '%m/%d/%Y'
CSV_HEADERS = ['Transaction Date','Post Date','Description','Category','Type','Amount','Memo']
CSV_CHECKING_HEADERS = ['Details','Posting Date','Description','Amount','Type','Balance','Check or Slip #']
START_DATE = None
END_DATE = None
CREDIT_CARD_ACCOUNT = 'credit'
CHECKING_ACCOUNT = 'checking'

def sort_and_filter(data):
    key = 'Transaction Date'
    if account_type == CHECKING_ACCOUNT:
        key = 'Posting Date'
    return sorted(
        filter(
            lambda d: (d[key] >= START_DATE if START_DATE else True) and (d[key] < END_DATE if END_DATE else True),
            data
        ),
        key=lambda d: d[key]
    )

class LineConverter(PDFPageAggregator):
    def __init__(self, rsrcmgr, pageno=1, laparams=None):
        PDFPageAggregator.__init__(self, rsrcmgr, pageno=pageno, laparams=laparams)
        self.result = {}

    def receive_layout(self, ltpage):        
        lines = {}
        def render(item):
            if isinstance(item, (LTPage, LTTextBox)):
                for child in item:
                    render(child)
            elif isinstance(item, LTTextLine):
                child_str = ''
                for child in item:
                    if isinstance(child, (LTChar, LTAnno)):
                        child_str += child.get_text()
                child_str = ' '.join(child_str.split()).strip()
                if child_str:
                    lines.setdefault((self.pageno, item.bbox[1]), []).append(child_str) # page number, bbox y1
                for child in item:
                    render(child)
            return

        render(ltpage)
        self.result = lines

    def get_result(self):
        return list(self.result.values())


def pdf_to_lines(file_name):
    data = []

    with open(file_name, 'rb') as fp:
        rsrcmgr = PDFResourceManager()
        device = LineConverter(rsrcmgr, laparams=LAParams())
        interpreter = PDFPageInterpreter(rsrcmgr, device)

        for page in PDFPage.get_pages(fp):
            interpreter.process_page(page)
            data.extend(device.get_result())

    return data


def translate_to_csv(lines):
    # Attempt to build a range of dates to append year
    year_guesser = [dt.today()]
    for row in lines:
        if len(row) == 2 and row[0] == 'Opening/Closing Date':
            opening, closing = row[1].split(' - ')
            year_guesser.append(dt.strptime(opening, '%m/%d/%y'))
            year_guesser.append(dt.strptime(closing, '%m/%d/%y'))

    # Sort it, the order is the order that each year will be guessed
    year_guesser.sort()

    # Regex pattern for matching month/day format of each line
    date_pattern = r'\d\d\/\d\d'
    csv_data = []

    for row in lines:
        if len(row) != 3 and len(row) != 4:
            continue
        if len(row) == 3:
            date, desc, amount = row
        else:
            date, desc, amount, balance = row
        if re.search(date_pattern, date):
            # Attempt to create dates
            for d in year_guesser:
                if date.split('/')[0] == d.strftime('%m'):
                    date = dt.strptime(date + '/' + d.strftime('%Y'), '%m/%d/%Y')
                    break
            else:
                date = dt.strptime(date + '/' + year_guesser[-1].strftime('%Y'), '%m/%d/%Y')

            trans_type = 'Sale' 
            # Clean up commas from currency ammount
            amount = amount.replace(',', '')
            # Amounts are reversed compared to CSVs, flip them
            if '-' in amount:
                trans_type = 'Return'
                amount = amount.strip('-')
            else:
                amount = '-' + amount
            # credit card
            if account_type == CREDIT_CARD_ACCOUNT:
                csv_data.append(dict(zip(CSV_HEADERS, [date, date, desc, '', trans_type, amount, ''])))
            # checking
            if account_type == CHECKING_ACCOUNT:
                csv_data.append(dict(zip(CSV_CHECKING_HEADERS, ['Details', date, desc, amount,  '', balance, ''])))

    return sort_and_filter(csv_data)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--start",
        default=None,
        help="The Start Date to filter items (inclusive), uses the date-format"
    )
    parser.add_argument(
        "--end",
        default=None,
        help="The End Date to filter items (exclusive), uses the date-format"
    )
    parser.add_argument(
        "--date-format",
        default=CSV_DATE_FORMAT,
        help="The output date format, default: '%(default)s'"
    )
    parser.add_argument(
        "--output",
        default='Extracted_Chase_Activity_Stmt_{}.csv'.format(dt.today().strftime('%Y%m%d')),
        help="The output filename"
    )
    parser.add_argument(
        "--dir",
        default='.',
        help="The directory to scan pdfs from"
    )
    parser.add_argument(
        "--type",
        default='credit',
        help="The type of pdfs, credit/checking"
    )

    args = parser.parse_args()

    CSV_DATE_FORMAT = args.date_format
    START_DATE = dt.strptime(args.start, CSV_DATE_FORMAT) if args.start else None
    END_DATE = dt.strptime(args.end, CSV_DATE_FORMAT) if args.end else None
    output_file = args.output
    account_type = args.type

    print('Started chase-ing PDFs:')
    input_files = []

    for file in os.listdir(args.dir):
        if file.endswith(".pdf"):
            input_files.append(os.path.join(args.dir, file))

    all_pdf_data = []
    num_rows = 0
    for file in input_files:
        data = translate_to_csv(pdf_to_lines(file))
        all_pdf_data.extend(data)
        print('Parsed {} rows from file: {}'.format(len(data), file))

    all_pdf_data = sort_and_filter(all_pdf_data)

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if account_type == CHECKING_ACCOUNT:
            writer = csv.DictWriter(f, fieldnames=CSV_CHECKING_HEADERS)
        writer.writeheader()
        # Format out datetime
        for row in all_pdf_data:
            if account_type == CHECKING_ACCOUNT:
                row['Posting Date'] = dt.strftime(row['Posting Date'], CSV_DATE_FORMAT)
            else:
                row['Transaction Date'] = dt.strftime(row['Transaction Date'], CSV_DATE_FORMAT)
            writer.writerow(row)

    print('CSV file generated with {} rows: {}'.format(
        len(all_pdf_data), output_file)
    )
