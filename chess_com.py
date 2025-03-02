import sys
import os
import re
import json
import math
from datetime import datetime
from collections import OrderedDict
import chess
import chess.pgn
import requests
from bs4 import BeautifulSoup

"""
ISSUES:
  Can't download games VS computer. Server error code 400. Tried manualy from the website and it does error.
"""
# Regex patterns
TOKEN_PAT = r'id="_token.+value="(.+)"'
ARCHIVE_TOTAL_PAGES_PAT = r'data-total-pages="(.+)"'
ARCHIVE_TOTAL_GAMES_PAT = r'Game History \((.+)\)"'
ARCHIVE_VS_COMPUTER_PAT = r'/computer/'
# Chess.com URLs
LOGIN_AND_GO_URL = 'https://www.chess.com/login_and_go'
LOGIN_CHECK_URL = 'https://www.chess.com/login_check'
LOGOUT_URL = 'https://www.chess.com/logout'
GAMES_ARCHIVE_URL = 'https://www.chess.com/games/archive'
GAMES_RAW_DATA_URL = 'https://www.chess.com/callback/game/pgn-info'
# Request headers to make chess.com think we're not a script
HEADERS = {
  # To prevent chess.com sending you an email about suspicious login use your own User-Agent line
  "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
  "Referer": "https://www.chess.com/login",
  "Origin": "https://www.chess.com",
}
# TCN decoding
TCN_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!?{~}(^)[_]@#$,./&-*++="
# Game result IDs
GAME_RESULT_IDS = {
  1:  {'win': True,  'resigned': False, 'draw': False, 'agreement': False, 'timeout': False, 'checkmated': False, 'abandoned': False},
  2:  {'win': False, 'resigned': False, 'draw': False, 'agreement': False, 'timeout': False, 'checkmated': True,  'abandoned': False},
  3:  {'win': False, 'resigned': False, 'draw': True,  'agreement': True,  'timeout': False, 'checkmated': True,  'abandoned': False},
  4:  {'win': False, 'resigned': False, 'draw': True,  'agreement': False, 'timeout': False, 'checkmated': False, 'abandoned': False},
  5:  {'win': False, 'resigned': False, 'draw': False, 'agreement': False, 'timeout': True,  'checkmated': False, 'abandoned': False},
  6:  {'win': False, 'resigned': True,  'draw': False, 'agreement': False, 'timeout': False, 'checkmated': False, 'abandoned': False},
  21: {'win': False, 'resigned': True,  'draw': False, 'agreement': False, 'timeout': False, 'checkmated': False, 'abandoned': True},
}
PIECE_MAP = {
  'p': chess.PAWN,
  'n': chess.KNIGHT,
  'b': chess.BISHOP,
  'r': chess.ROOK,
  'q': chess.QUEEN,
  'k': chess.KING
}
# Local downloads
DOWNLOADS_DIR = 'downloads'
RAW_GAMES_DIR = os.path.join(DOWNLOADS_DIR, 'raw_games')
PGN_GAMES_DIR = os.path.join(DOWNLOADS_DIR, 'pgn_games')

# The Session object used for all HTTP requests
session = requests.Session()

# Helper functions
def get(session, url, headers):
  response = session.get(url, headers=headers)
  return response

def post(session, url, headers):
  response = session.post(url, headers=headers)
  return response

def dump_response(response, path):
  with open(path, 'w') as f:
    f.write(response.text)

def decode_tcn(n):
  piece_chars = "qnrbkp"
  o = 0
  s = 0
  u = 0
  w = len(n)
  c = []
  for i in range(0, w, 2):
    u = {
      "from": None,
      "to": None,
      "drop": None,
      "promotion": None,
    }
    o = TCN_CHARS.index(n[i])
    s = TCN_CHARS.index(n[i + 1])
    if s > 63:
      u["promotion"] = piece_chars[math.floor((s - 64) / 3)]
      s = o + (-8 if o < 16 else 8) + ((s - 1) % 3) - 1
    if o > 75:
      u["drop"] = piece_chars[o - 79]
    else:
      u["from"] = TCN_CHARS[o % 8] + str(math.floor(o / 8) + 1)
    u["to"] = TCN_CHARS[s % 8] + str(math.floor(s / 8) + 1)
    c.append(u)
  return c

def generate_pgn(uci, headers):
  game = chess.pgn.Game()
  for h in headers:
    game.headers[h] = str(headers[h])
  node = game
  for move_idx, i in enumerate(uci):
    drop = i.get("drop")
    mapped_drop = PIECE_MAP[drop] if drop is not None else None
    promotion = i.get("promotion")
    mapped_promotion = PIECE_MAP[promotion] if promotion is not None else None
    from_square = chess.parse_square(i["from"])
    to_square = chess.parse_square(i["to"])
    move = chess.Move(from_square=from_square, to_square=to_square, drop=mapped_drop, promotion=mapped_promotion)
    if (move_idx==0):
      node = game.add_variation(chess.Move.from_uci(str(move)))
    else:
      node = node.add_variation(chess.Move.from_uci(str(move)))
  return game

def get_game_result(p1, p2):
  digit1 = '1' if p1 == 1 else '0'
  digit2 = '1' if p2 == 1 else '0'
  return '-'.join([digit1, digit2])

def get_game_termination(name1, p1, name2, p2):

  if p1 == 1:
    result = name1 + " won"
  elif p2 == 1:
    result = name2 + " won"
  else:
    result = "draw"
  return result

class Downloads:
  def __init__(self):
    if not os.path.exists(DOWNLOADS_DIR):
      os.makedirs(DOWNLOADS_DIR)
    if not os.path.exists(RAW_GAMES_DIR):
      os.makedirs(RAW_GAMES_DIR)
    if not os.path.exists(PGN_GAMES_DIR):
      os.makedirs(PGN_GAMES_DIR)
    # Find already downloaded game archive pages
    filenames = sorted(os.listdir(RAW_GAMES_DIR))
    if not len(filenames):
      self.last_page_downloaded = 0
    else:
      self.last_page_downloaded = int(filenames[-1].split('_')[1])

  def save_raw_games_json(self, text, page_num):
    page_num_str = str(page_num).zfill(4)
    path = os.path.join(RAW_GAMES_DIR, "page_%s_games.json" % page_num_str)
    with open(path, 'w') as f:
      f.write(text)

  def load_raw_games_json(self, page_num):
    page_num_str = str(page_num).zfill(4)
    path = os.path.join(RAW_GAMES_DIR, "page_%s_games.json" % page_num_str)
    if not os.path.exists(path):
      print('Raw game %s does not exist in downloaded games.' % path)
      return
    with open(path, 'r') as f:
      data = json.loads(f.read())
      return data

  def save_games_pgn(self, text, page_num):
    page_num_str = str(page_num).zfill(4)
    path = os.path.join(PGN_GAMES_DIR, "page_%s_games.pgn" % page_num_str)
    with open(path, 'w') as f:
      f.write(text)

class ChessGame:
  def __init__(self, id=None, uuid=None, type=None):
    self.id = id
    self.uuid = uuid
    self.type = type
    self.name = None
    self.is_vs_computer = False
    self.start_date = None
    self.player_1_name = None
    self.player_2_name = None
    self.time_control = None
    self.tcn_moves = None
    self.move_timestamps = None
    self.initial_setup = None
    self.variant = None
    self.player_1_result_id = None
    self.player_2_result_id = None
    self.round = None
    self.first_move_ply = None
    self.variant_id = None
    self.white_rating = None
    self.black_rating = None
    self.end_time = None
    self.pgn_text = None

  def parse_from_data(self, data):
    if self.id and str(data['gameId']) != self.id:
      print("Parsing game json but IDs don't match! Game id: %s but data id: %d" % (self.id, data['gameId']))
      sys.exit()
    self.id = data['gameId']
    self.name = data['name']
    self.start_date = data['startDate']
    self.player_1_name = data['player1Name']
    self.player_2_name = data['player2Name']
    self.time_control = data['timeControl']
    self.tcn_moves = data['tcnMoves']
    self.move_timestamps = data['moveTimestamps']
    self.initial_setup = data['initialSetup']
    self.variant = data['variant']
    self.player_1_result_id = data['player1ResultID']
    self.player_2_result_id = data['player2ResultID']
    self.round = data['round']
    self.first_move_ply = data['firstMovePly']
    self.variant_id = data['variantId']
    self.white_rating = data['whiteRating']
    self.black_rating = data['blackRating']
    self.end_time = data['endTime']

  @property
  def pgn_headers(self):
    d = OrderedDict()
    d['Event'] = self.name
    d['Site'] = 'Chess.com'
    d['Date'] = datetime.fromtimestamp(self.start_date).strftime('%Y.%m.%d')
    d['Round'] = self.round
    d['White'] = self.player_1_name
    d['Black'] = self.player_2_name
    d['Result'] = get_game_result(self.player_1_result_id, self.player_2_result_id)
    d['WhiteElo'] = self.white_rating
    d['BlackElo'] = self.black_rating
    d['TimeControl'] = self.time_control
    d['EndTime'] = self.end_time
    d['Termination'] = get_game_termination(self.player_1_name, self.player_1_result_id,
                                            self.player_2_name, self.player_2_result_id)
    return d

  @property
  def pgn(self):
    if self.pgn_text:
      return self.pgn_text
    if not self.tcn_moves:
      print("Game data not loaded for game", self)
      return
    moves = decode_tcn(self.tcn_moves)

    game = chess.Game()

  def __str__(self):
    return "%s VS %s, id: %s, uuid: %s, type: %s" % \
           (self.player_1_name, self.player_2_name, self.id, self.uuid, self.type)

class GamesArchive:
  def __init__(self, chesscom):
    self.chesscom = chesscom
    self.total_games = None
    self.total_pages = None
    self.downloads = Downloads()

  def load(self):
    response = session.get(GAMES_ARCHIVE_URL, headers=HEADERS)
    # Get number of games played
    match = re.search(ARCHIVE_TOTAL_GAMES_PAT, response.text)
    if not match:
      print("ERROR: Couldn't find total number of games in archive!")
      sys.exit()
    total_games_string = match.group(1).replace(',', '')
    self.total_games = int(total_games_string)
    # Get number of pages in archive
    match = re.search(ARCHIVE_TOTAL_PAGES_PAT, response.text)
    if not match:
      print("ERROR: Couldn't find total number of pages in games archive!")
      sys.exit()
    self.total_pages = int(match.group(1))
    # soup = BeautifulSoup(response.text, 'html.parser')

  def list_games_on_page(self, page=1):
    url = GAMES_ARCHIVE_URL + "?page=%d" % page
    response = session.get(url, headers=HEADERS)

    if response.status_code == 429:
      print("Server said: Too many requests sent too fast!")
      sys.exit()

    if response.status_code != 200:
      print("Failed listing archive games at page %d... URL %s" % (page, url))

    soup = BeautifulSoup(response.text, 'html.parser')
    games_div = soup.find(id='games-root-index')
    tbody = games_div.find('tbody')
    rows = tbody.find_all('tr')
    for row in rows:
      # Game IDs and types
      checkbox = row.find('input', type='checkbox')
      game_id = checkbox['data-game-id']
      game_uuid = checkbox['data-game-uuid']
      game_type = checkbox['data-game-type']
      # Create the ChessGame
      game = ChessGame(game_id, game_uuid, game_type)
      # Type of game (Live, Computer...)
      icon_cell = row.find('td', class_='archive-games-icon-block')
      icon_cell_link = icon_cell.find('a', class_='archive-games-background-link')
      game_url = icon_cell_link['href']
      vs_computer_match = re.search(ARCHIVE_VS_COMPUTER_PAT, game_url)
      if vs_computer_match:
        game.is_vs_computer = True
      # Update game with users/players
      users_div = row.find('div', class_='archive-games-users')
      user_divs = users_div.find_all('div', class_='archive-games-user-info')
      game.player_1_name = user_divs[0].find('a').string.strip()
      game.player_2_name = user_divs[1].find('a').string.strip()
      yield game

class ChessCom:
  def __init__(self, username=None, password=None):
    while not username:
      username = input('Enter your username (or email): ')
    while not password:
      password = input('Enter your password: ')
    self.username = username
    self.password = password
    self.loged_in = False
    self.token = None
    self.archive = GamesArchive(self)

  def get_token(self):
    response = session.get(LOGIN_AND_GO_URL, headers=HEADERS)
    match = re.search(TOKEN_PAT, response.text)
    if not match:
      print("Couldn't find token!")
      return
    self.token = match.group(1)
    return self.token

  """
  login() sometimes fails (with correct credentials)
  chess.com may requires some time to pass between logins.
  TODO: try to logout at the end of the script!
  """
  def login(self, username=None, password=None):
    username = username or self.username
    password = password or self.password
    token = self.get_token()
    login_payload = {
      "_username": username,
      "_password": password,
      "_remember_me": 1,
      "login": "",
      "_token": token,
    }
    response = session.post(LOGIN_CHECK_URL, data=login_payload, headers=HEADERS)
    if response.status_code != 200:
      print("Login failed with error code %d. Check your credentials and try again." % response.status_code)
      sys.exit()
    self.loged_in = True
    return True

  """
  TODO: find the CSRF_TOKEN required for logout (not self.token)
  """
  def logout(self):
    response = session.post(LOGOUT_URL, data={'_csrf_token': self.token})
    success = response.status_code == 302
    print("Logout success:", success)

  def fetch_raw_games_data(self, games):
    ids = []
    uuids = []
    types = []

    for game in games:
      if game.is_vs_computer:
        continue  # Games VS computer error out when data fetched
      ids.append(game.id)
      uuids.append(game.uuid)
      types.append(game.type)

    payload = {
      'ids': ','.join(ids),
      'types': ','.join(types),
      'uuids': uuids,
      '_token': self.token,
    }
    # Fetch and check response
    response = session.post(GAMES_RAW_DATA_URL, data=payload)
    if response.status_code != 200:
      print("ERROR: Faild fetching raw games data! Response code: %d" % response.status_code)
      print("Payload:")
      print(payload)
      print(len(ids), "IDs", len(uuids), "UUIDs", len(types), "types")
      sys.exit()

    return response.text


