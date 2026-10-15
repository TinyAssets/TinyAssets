"""Private CLI credential result; exit status follows custody writeback."""


class AuthWriteback(dict):
    def __init__(self, files, exit_code):
        super().__init__(files)
        self.exit_code = exit_code
