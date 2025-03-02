import os
import time
import random
import json
from chess_com import ChessCom, ChessGame, generate_pgn, decode_tcn

def main():
  chesscom = ChessCom()
  chesscom.login()

  archive = chesscom.archive
  archive.load()  # Load games archive page and parse relevant info

  for page_num in range(archive.downloads.last_page_downloaded + 1, archive.total_pages + 1):
    print('Downloading games from page', page_num)
    games = list(archive.list_games_on_page(page_num))
    time.sleep(1)
    games_data = chesscom.fetch_raw_games_data(games)
    archive.downloads.save_raw_games_json(games_data, page_num)
    # Convert raw games data to PGN games
    games_json = json.loads(games_data)
    pgn_games = []
    for game_data in games_json:
      game = ChessGame()
      game.parse_from_data(game_data)
      tcn_moves = decode_tcn(game.tcn_moves)
      pgn = generate_pgn(tcn_moves, game.pgn_headers)
      pgn_games.append(str(pgn))
    # Compile all games from current archive page to a single PGN string
    joined_pgns = '\n\n'.join(pgn_games)
    archive.downloads.save_games_pgn(joined_pgns, page_num)
    time.sleep(3)

if __name__ == "__main__":
  main()
