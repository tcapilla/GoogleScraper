HOWTO Add New Scapers
=====================

Assuming your search engine is named `FooBar`, the following is how
you can add a basic custom scraper to GoogleScaper. This is not an
exhaustive guide.

1. In `config.cfg`,

   1. add the name of your search engine to
      `supported_search_engines`, preferably lowercase, e.g.,
      `foobar`.

   2. add a new key of the form `foobar_search_url`

      This is the base endpoint to the search engine (and presumably
      the search results).

2. In `http_mode.py`,

   1. add a new section specifying the search query parameters for
      your search engine to `get_GET_params_for_search_engine`. It
      should be roughly of the form::

        elif search_engine = 'foobar':
            search_engine[<search query string key>] = query

3. In `parsing.py`,

   1. Add a search engine parser class. For exammple::

        class FooBarParser(Parser):
            """Parses SERP pages of the FooBar search engine."""

            search_engine = 'foobar'

            search_types = ['normal']

            effective_query_selector = ['']
            
            no_results_selector = []

            num_results_search_selectors = []

            normal_search_selectors = {
                'results': {
                    'us_ip': {
                        'container': '#results_page',
                        'result_container': '.results',
                        'link': '.fb-link > a::attr(href)',
                        'snippet': '.fb-snippet',
                        'title': '.fb-title > a::text',
                }
            },
        }

   2. add a regex to `get_parser_by_url`, e.g.::

        if re.search(r'^http[s]?://www\.foobar', url):
            parser = FooBarParser

   3. add the name of your search engine to
      `get_parser_by_search_engine`, e.g.::

        elif search_engine == 'foobar':
            return FooBarParser

4. In `database.py`,

   1. add custom columns to `Link` class which is the model for the
      `link` table in the generated database containing scraped data, e.g., 

   2. change `SearchEngineResultsPage.set_values_from_parser` to load
      the contents of the parsed data of the page into the Link table.

      
Adding Selenium Scrapers
========================

1. Selenium requires the chromedriver. You may download it `here
   <https://sites.google.com/a/chromium.org/chromedriver/home>`_.

   Make sure that the extracted binary is somewhere in the PATH of
   the scraper.

2. 
   
You may also edit `scraping.py` if you intend on using Selenium.)

