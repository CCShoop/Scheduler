from datetime import datetime


def get_log_datetime():
    return datetime.now().astimezone().strftime('[%m/%d/%Y] [%H:%M:%S]')


def log(string: str = ''):
    print(f'{get_log_datetime()} {string}')

def log_info(string: str = ''):
    string = 'INFO> ' + string
    log(string)

def log_warn(string: str = ''):
    string = 'WARN> ' + string
    log(string)

def log_error(string: str = ''):
    string = 'ERROR> ' + string
    log(string)

def log_debug(string: str = ''):
    string = 'DEBUG> ' + string
    log(string)
