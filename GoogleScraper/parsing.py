# -*- coding: utf-8 -*-

import sys
import os
import re
import lxml.html
from lxml.html.clean import Cleaner
import logging
from urllib.parse import unquote, urlparse
import pprint
from GoogleScraper.database import SearchEngineResultsPage, generate_id
from GoogleScraper.config import Config
from GoogleScraper.log import out
from cssselect import HTMLTranslator
from datadog import initialize, api


logger = logging.getLogger('GoogleScraper')
initialize(**Config['DATADOG_KEYS'])


class InvalidSearchTypeException(Exception):
    pass


class UnknowUrlException(Exception):
    pass


class NoParserForSearchEngineException(Exception):
    pass


class Parser():
    """Parses SERP pages.

    Each search engine results page (SERP) has a similar layout:
    
    The main search results are usually in a html container element (#main, .results, #leftSide).
    There might be separate columns for other search results (like ads for example). Then each 
    result contains basically a link, a snippet and a description (usually some text on the
    target site). It's really astonishing how similar other search engines are to Google.
    
    Each child class (that can actual parse a concrete search engine results page) needs
    to specify css selectors for the different search types (Like normal search, news search, video search, ...).

    Attributes:
        search_results: The results after parsing.
    """

    # this selector specified the element that notifies the user whether the search
    # had any results.
    no_results_selector = []

    # if subclasses specify an value for this attribute and the attribute
    # targets an element in the serp page, then there weren't any results
    # for the original query.
    effective_query_selector = []

    # the selector that gets the number of results (guessed) as shown by the search engine.
    num_results_search_selectors = []

    # some search engine show on which page we currently are. If supportd, this selector will get this value.
    page_number_selectors = []

    # The supported search types. For instance, Google supports Video Search, Image Search, News search
    search_types = []

    # Each subclass of Parser may declare an arbitrary amount of attributes that
    # follow a naming convention like this:
    # *_search_selectors
    # where the asterix may be replaced with arbitrary identifier names.
    # Any of these attributes represent css selectors for a specific search type.
    # If you didn't specify the search type in the search_types list, this attribute
    # will not be evaluated and no data will be parsed.

    def __init__(self, html=None, query=''):
        """Create new Parser instance and parse all information.

        Args:
            html: The raw html from the search engine search. If not provided, you can parse 
                    the data later by calling parse(html) directly.
            searchtype: The search type. By default "normal"
            
        Raises:
            Assertion error if the subclassed
            specific parser cannot handle the the settings.
        """
        self.searchtype = Config['SCRAPING'].get('search_type', 'normal')
        assert self.searchtype in self.search_types, 'search type "{}" is not supported in {}'.format(
            self.searchtype,
            self.__class__.__name__
        )

        self.query = query
        self.html = html
        self.dom = None
        self.search_results = {}
        self.num_results_for_query = ''
        self.num_results = 0
        self.effective_query = ''
        self.page_number = -1
        self.no_results = False

        # to be set by the implementing sub classes
        self.search_engine = ''

        # short alias because we use it so extensively
        self.css_to_xpath = HTMLTranslator().css_to_xpath

        if self.html:
            self.parse()

    def parse(self, html=None):
        """Public function to start parsing the search engine results.
        
        Args: 
            html: The raw html data to extract the SERP entries from.
        """
        if html:
            self.html = html

        # lets do the actual parsing
        print("Parsing the SERP for {keyword}...".format(keyword=self.query))
        self._parse()

        # Apply subclass specific behaviour after parsing has happened
        # This is needed because different parsers need to clean/modify
        # the parsed data uniquely.
        self.after_parsing()

    def _parse_lxml(self, cleaner=None):
        try:
            parser = lxml.html.HTMLParser(encoding='utf-8')
            if cleaner:
                self.dom = cleaner.clean_html(self.dom)
            self.dom = lxml.html.document_fromstring(self.html, parser=parser)
            self.dom.resolve_base_href()
        except Exception as e:
            # maybe wrong encoding
            logger.error(e)

    def _parse(self, cleaner=None):
        print("SERP for {keyword} length = {htmllen}".format(
            keyword=self.query,
            htmllen=len(self.html)))
        """Internal parse the dom according to the provided css selectors.
        
        Raises: InvalidSearchTypeException if no css selectors for the searchtype could be found.
        """
        self._parse_lxml(cleaner)

        # Try to parse the number of results
        attr_name = self.searchtype + '_search_selectors'
        selector_dict = getattr(self, attr_name, None)

        # Get the appropriate css selectors for the num_results for the keyword
        num_results_selector = getattr(self, 'num_results_search_selectors', None)

        self.num_results_for_query = self.first_match(num_results_selector, self.dom)
        if not self.num_results_for_query:
            out('{}: Cannot parse num_results from serp page with selectors {}'.format(self.__class__.__name__,
                                                                                       num_results_selector), lvl=4)

        # Get the current page we are at (sometimes search engines don't show this)
        try:
            self.page_number = int(self.first_match(self.page_number_selectors, self.dom))
        except ValueError:
            self.page_number = -1

        # Let's see if the search query was shitty (no results for that query)
        self.effective_query = self.first_match(self.effective_query_selector, self.dom)
        if self.effective_query:
            out('{}: There was no search hit for the search query. Search engine used {} instead.'.format(
                self.__class__.__name__, self.effective_query), lvl=4)

        # The element that notifies the user about no results
        self.no_results_text = self.first_match(self.no_results_selector, self.dom)

        # Get the stuff that is of interest in SERP pages
        if not selector_dict and not isinstance(selector_dict, dict):
            raise InvalidSearchTypeException('There is no such attribute: {}. No selectors found'.format(attr_name))

        #
        # Where it allll happens...
        #
        for result_type, selector_class in selector_dict.items():
            
            self.search_results[result_type] = []
            
            for selector_specific, selectors in selector_class.items():
                
                if 'result_container' in selectors and selectors['result_container']:
                    css = '{container} {result_container}'.format(**selectors)
                else:
                    css = selectors['container']

                results = self.dom.xpath(
                    self.css_to_xpath(css) # This is where the css selection is compiled to xpath.
                )

                print("* Scraping {result_type} variation {selector_specific} [{results} results]...".format(
                    result_type=result_type,
                    selector_specific=selector_specific,
                    results=len(results)))
                
                to_extract = set(selectors.keys()) - {'container', 'result_container'}
                selectors_to_use = {key: selectors[key] for key in to_extract if key in selectors.keys()}
                
                current_rank = 1
                for result in results:
                    # Let's add primitive support for CSS3 pseudo selectors
                    # We just need two of them
                    # ::text
                    # ::attr(attribute)

                    # You say we should use xpath expressions instead?
                    # Maybe you're right, but they are complicated when it comes to classes,
                    # have a look here: http://doc.scrapy.org/en/latest/topics/selectors.html
                    serp_result = {}
                    # key are for example 'link', 'snippet', 'visible-url', ...
                    # selector is the selector to grab these items
                    for key, selector in selectors_to_use.items():
                        serp_result[key] = self.advanced_css(selector, result)

                    # Only add when link is not None and no
                    # duplicates. If a duplicate result does exist but
                    # have a visible link that was missing previously,
                    # replace old one.
                    print("\t- {rank}. {visible_link}".format(
                        rank=current_rank,
                        visible_link=serp_result.get('visible_link')))
                    if 'link' in serp_result and serp_result['link'] and \
                       not [ e for e in self.search_results[result_type] if e['link'] == serp_result['link'] ]:
                        self.search_results[result_type].append(serp_result)
                        serp_result['rank'] = current_rank
                        print("\t  [NEWLINK] {visible_link}".format(
                            visible_link=serp_result.get('visible_link')))
                        current_rank += 1
                        self.num_results += 1
                    elif 'link' in serp_result and serp_result['link'] and \
                         'visible_link' in serp_result and serp_result['visible_link']:

                        vlinks = [ e for e in self.search_results[result_type] if e['link'] == serp_result['link']
                                   and e['visible_link'] is None ]
                        
                        if vlinks:
                            vl = vlinks[0] # should only be one
                            print("WARNING: {n_vlinks} matches [{vlinks}]".format(
                                n_vlinks=len(vlinks),
                                vlinks=", ".join(map(str, [ e['visible_link'] for e in vlinks ]))))
                            
                            serp_result['rank'] = vl['rank']
                            vl_index = self.search_results[result_type].index(vl)
                            print("\t  [REPLACE] ({rank}. {old_vlink})".format(
                                rank=serp_result['rank'],
                                old_vlink=self.search_results[result_type][vl_index]))
                            self.search_results[result_type][vl_index] = serp_result


                    for restype, res in self.search_results.items():
                        api.Metric.send(metric="l2wr.{rt}".format(rt=restype),
                                        points=len(res),
                                        tags=["keyword:{kw}".format(kw=self.query)])


    def advanced_css(self, selector, element):
        """Evaluate the :text and ::attr(attr-name) additionally.

        Args:
            selector: A css selector.
            element: The element on which to apply the selector.

        Returns:
            The targeted element.

        """
        value = None

        if selector.endswith('::text'):
            try:
                value = element.xpath(self.css_to_xpath(selector.split('::')[0]))[0].text_content()
            except IndexError:
                pass
        else:
            match = re.search(r'::attr\((?P<attr>.*)\)$', selector)

            if match:
                attr = match.group('attr')
                try:
                    value = element.xpath(self.css_to_xpath(selector.split('::')[0]))[0].get(attr)
                except IndexError:
                    pass
            else:
                try:
                    value = element.xpath(self.css_to_xpath(selector))[0].text_content()
                except IndexError:
                    pass

        return value

    def first_match(self, selectors, element):
        """Get the first match.

        Args:
            selectors: The selectors to test for a match.
            element: The element on which to apply the selectors.

        Returns:
            The very first match or False if all selectors didn't match anything.
        """
        assert isinstance(selectors, list), 'selectors must be of type list!'

        for selector in selectors:
            if selector:
                try:
                    match = self.advanced_css(selector, element=element)
                    if match:
                        return match
                except IndexError as e:
                    pass

        return False

    def after_parsing(self):
        """Subclass specific behaviour after parsing happened.
        
        Override in subclass to add search engine specific behaviour.
        Commonly used to clean the results.
        """

    def __str__(self):
        """Return a nicely formatted overview of the results."""
        return pprint.pformat(self.search_results)

    @property
    def cleaned_html(self):
        # Try to parse the provided HTML string using lxml
        # strip all unnecessary information to save space
        cleaner = Cleaner()
        cleaner.scripts = True
        cleaner.javascript = True
        cleaner.comments = True
        cleaner.style = True
        self.dom = cleaner.clean_html(self.dom)
        assert len(self.dom), 'The html needs to be parsed to get the cleaned html'
        return lxml.html.tostring(self.dom)

    def iter_serp_items(self):
        """Yields the key and index of any item in the serp results that has a link value"""

        for key, value in self.search_results.items():
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict) and item['link']:
                        yield (key, i)


"""
Here follow the different classes that provide CSS selectors 
for different types of SERP pages of several common search engines.

Just look at them and add your own selectors in a new class if you
want the Scraper to support them.

You can easily just add new selectors to a search engine. Just follow
the attribute naming convention and the parser will recognize them:

If you provide a dict with a name like finance_search_selectors,
then you're adding a new search type with the name finance.

Each class needs a attribute called num_results_search_selectors, that
extracts the number of searches that were found by the keyword.

Please note:
The actual selectors are wrapped in a dictionary to clarify with which IP
they were requested. The key to the wrapper div allows to specify distinct
criteria to whatever settings you used when you requested the page. So you
might add your own selectors for different User-Agents, distinct HTTP headers, what-
ever you may imagine. This allows the most dynamic parsing behaviour and makes
it very easy to grab all data the site has to offer.
"""


class GoogleParser(Parser):
    """Parses SERP pages of the Google search engine."""

    search_engine = 'google'

    search_types = ['normal', 'image']

    effective_query_selector = ['#topstuff .med > b::text']

    no_results_selector = []

    num_results_search_selectors = ['#resultStats']

    page_number_selectors = ['#navcnt td.cur::text']

    normal_search_selectors = {
        'organic': {
            '0': {
                'container': '#center_col',
                'result_container': 'div.g ',  
                'link': 'h3.r > a:first-child::attr(href)',
                'snippet': 'div.s span.st::text',
                'title': 'h3.r > a:first-child::text',
                'visible_link': 'cite::text'
            }
        },
        'ads_top': {
            '0': {
                'container': '#_Ltg',
                'result_container': '.ads-ad',
                'title': 'h3 > a::text',
                'link': 'h3 > a::attr(href)',
                'visible_link': '.ads-visurl > cite',
                'content': '.ads-creative'
            },
                
            # Mobile
            '1': {
                'container': '#tads',
                'result_container': '.ads-ad',
                'title': '._uWj > h3',
                'link': 'a[id$="s0p"]::attr(href)',
                'visible_link': '.ads-visurl > cite',
                'content': '.ads-creative'
                    
            },
        },
        'ads_bottom': {
            '0': {
                'container': '#_Ktg',
                'result_container': '.ads-ad',
                'title': 'h3 > a::text',
                'link': 'h3 > a::attr(href)',
                'visible_link': '.ads-visurl > cite',
                'content':'.ads-creative'
            },

            # Mobile
            '1': {
                'container': '#tadsb',
                'result_container': '.ads-ad',
                'title': '._uWj > h3',
                'link': 'a[id$="s3p"]::attr(href)',
                'visible_link': '.ads-visurl > cite',
                'content': '.ads-creative'

            },
        },
        'pla_main': {
            '0': {
                'container': '#center_col > table.ts',
                'result_container': 'td[valign="top"]',
                'title': 'div:nth-child(2) > a::text',
                'link': 'div:nth-child(2) > a::attr(href)',
                'price': 'div:nth-child(3)',
                'store': 'div:nth-child(4) > cite'
            },

            # Mobile
            '1': {
                'container': '.shopping-carousel-container',
                'result_container': '.pla-unit-container',
                'title': 'h4._HLg',
                'link': 'a.pla-unit::attr(href)',
                'price': '._XJg',
                'store': '._FLg'
            }
        },
        'pla_side': {
            '0': {
                'container': '#rhs_block > table.ts',
                'result_container': 'td[valign="top"]',
                'title': '._cf > div:nth-child(2) > a::text',
                'link': '._cf > div:nth-child(2) > a::attr(href)',
                'price': '._cf > div:nth-child(3)',
                'store': '._cf > div:nth-child(4) > cite'
            }
        }
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def after_parsing(self):
        """Clean the urls.
        
        A typical scraped results looks like the following:
        
        '/url?q=http://www.youtube.com/user/Apple&sa=U&ei=\
        lntiVN7JDsTfPZCMgKAO&ved=0CFQQFjAO&usg=AFQjCNGkX65O-hKLmyq1FX9HQqbb9iYn9A'
        
        Clean with a short regex.
        """
        super().after_parsing()

        if self.searchtype == 'normal':
            if self.num_results > 0:
                self.no_results = False
            elif self.num_results <= 0:
                self.no_results = True

            if 'No results found for' in str(self.html) or 'did not match any documents' in str(self.html):
                self.no_results = True

            # finally try in the snippets
            if self.no_results is True:
                for key, i in self.iter_serp_items():

                    if 'snippet' in self.search_results[key][i] and self.query:
                        if self.query.replace('"', '') in self.search_results[key][i]['snippet']:
                            self.no_results = False

        clean_regexes = {
            'normal': r'/url\?q=(?P<url>.*?)&sa=U&ei=',
            'image': r'imgres\?imgurl=(?P<url>.*?)&'
        }

        for key, i in self.iter_serp_items():
            result = re.search(
                clean_regexes[self.searchtype],
                self.search_results[key][i]['link']
            )
            if result:
                self.search_results[key][i]['link'] = unquote(result.group('url'))

            use_visible = False
            actual_link = self.search_results[key][i]['link']
            if actual_link:
                if not re.match('http', actual_link):
                    actual_link = 'http://' + actual_link
                parsed_url = urlparse(actual_link)
                if not parsed_url.netloc:
                    use_visible = True
            else:
                use_visible = True
                
            if use_visible and self.search_results[key][i].get('visible_link'):
                vlink = str.strip(self.search_results[key][i]['visible_link'])
                try:
                    vlink = vlink.split()[0]
                except:
                    pass
                self.search_results[key][i]['visible_link'] = 'http://' + vlink # Tweak this and Baidu
                self.search_results[key][i]['link'] = self.search_results[key][i]['visible_link']


class BaiduParser(Parser):
    """Parses SERP pages of the Baidu search engine."""

    search_engine = 'baidu'

    search_types = ['normal', 'image']

    num_results_search_selectors = ['#container .nums']

    no_results_selector = []

    # no such thing for baidu
    effective_query_selector = ['']

    page_number_selectors = ['.fk_cur + .pc::text']

    normal_search_selectors = {
        'organic': {
            '0': {
                'container': '#content_left',
                'result_container': '.c-container',
                'title': 'h3 > a::text',
                'link': 'h3 > a::attr(href)',
                'snippet': '.c-abstract::text',
                'visible_link': '.c-showurl::text'
            },
        },
        
        'brand_zone': {
            '0': {
                'container': '#content_left',
                'result_container': 'div[class$="-0-0"]',
                'title': 'a[class$="-header-title"]::text',
                'link': 'a[class$="-header-title"]::attr(href)',
                'snippet': 'div[id$="-description"]::text',
                'visible_link': 'div[class$="-site"]::text'
            } ,
            '1': {
                'container': '#content_left',
                'result_container': 'div[class$="-h1"]',
                'title': 'h2 > a[class$="-header-title"]::text',
                'link': 'h2 > a[class$="-header-title"]::attr(href)',
                'snippet': 'div[id$="-description"]',
                'visible_link': 'div[class$="-site"]'
            },
        },
        
        'brand_zone_side': {
            # '0': {
            #     'container': 'td[align="left"] > div:first-child',
            #     'result_container': 'div:nth-child(3) > div:nth-child(1)',
            #     'link': 'div:nth-child(1) a::attr(href)',
            #     'snippet': 'div:nth-child(3) a::text',
            #     'title': 'div:nth-child(1) a::text',
            #     'visible_link': 'div:nth-child(5) a::text'
            # },
            '1': {
                'container': '#content_right',
                'result_container': 'td[align="left"] > div > div > div',
                'title': 'div[class$="-title"] > h2[id$="-h2"] > a::text',
                'link': 'div[class$="-title"] > h2[id$="-h2"] > a::attr(href)',
                'snippet': 'div[class$="-htmltext"] > p[class$="-htmltext-desc"] > a::text',
                'visible_link': 'div[class$="-show-url"] > div[class$="-site"] > a::text'
            },
            '2': {
                'container': '#content_right',
                'result_container': 'td > div > div',
                'title': 'FAKETAG',
                'link': 'div[class$="-atom-htmltext"] > p[class$="-htmltext-desc"] > a::attr(href)',
                'snippet': 'div[class$="-atom-htmltext"] > p[class$="-htmltext-desc"] > a::text',
                'visible_link': 'FAKETAG'
            },
            '3': {
                'container': '#content_right',
                'result_container': 'td[align="left"] > div > div > div',
                'title': 'div[class$="-atom-htmltext"] > p[class$="-htmltext-desc"] > a::text',
                'link': 'div[class$="-atom-htmltext"] > p[class$="-htmltext-desc"] > a::attr(href)',
                'snippet': 'div[class$="-htmltext"] > p[class$="-htmltext-desc"] > a::text',
                'visible_link': 'div[class$="-show-url"] > div[class$="-site"] > a::text'
            },
            '4': {
                'container': '#content_right',
                'result_container': 'td > div > div',
                'title': 'FAKETAG',
                'link': 'div[class$="-atom-htmltext"] > p[class$="-htmltext-desc"] > a::attr(href)',
                'snippet': 'div[class$="-atom-htmltext"] > p[class$="-htmltext-desc"] > a::text',
                'visible_link': 'div[class$="-show-url"] > div[class$="-site"] > a::text'
            },
        },

        'promo_ads_side': {
            '0': {
                'container': '.ad-widget',
                'result_container': '.ec-figcaption',
                'link': 'h2 > a::attr(href)',
                'snippet': '.ec-description-link',
                'title': 'h2 > a::text',
                'visible_link': '.ec-footer::text'
            }
        },

        'ads_side': {
            '1': {
                'container': '#ec_im_container',
                'result_container': 'div[id^="bdfs"]',
                'title': 'a[id^="dfs"]::text',
                'link': 'a[id^="dfs"]::attr(href)',
                'snippet': 'a[id^="bdfs"] > font:nth-child(2)',
                'visible_link': 'a[id^="bdfs"] > font:nth-child(4)'
            },
        },

        'ads_bottom': {
            '0': {
                'container': '#content_left',
                'result_container': 'div[id^="50"]',
                'link': 'h3 > a::attr(href)',
                'snippet': 'div > a::text',
                'title': 'h3 > a::text',
                'visible_link': 'a > span::text'
            },
            
            '1': { 
                'container': '#content_left',
                'result_container': 'div[id^="50"]',
                'title': 'div:nth-child(1) > h3 > a::text',
                'link': 'div:nth-child(1) > h3 > a::attr(href)',
                'snippet': 'div:nth-child(2) > a::text',
                'visible_link': 'div:nth-child(3) > a > span'
            },
            '2': {
                'container': '#content_left',
                'result_container': 'div[id^="50"]',
                'title': 'div:nth-child(1) > h3 > a::text',
                'link': 'div:nth-child(1) > h3 > a::attr(href)',
                'snippet': 'div:nth-child(2) tr:nth-child(2) > div > font > a::text',
                'visible_link': 'div:nth-child(2) tr:nth-child(2) > div > div > a > span'
            },
            '3': { 
                'container': '#content_left',
                'result_container': 'div[id^="50"]',
                'title': 'div:nth-child(1) > h3 > a::text',
                'link': 'div:nth-child(1) > h3 > a::attr(href)',
                'snippet': 'div:nth-child(2) > a::text',
                'visible_link': 'div:nth-child(3) > a > span'
            },
            '4': {
                'container': '#content_left',
                'result_container': 'div[id^="50"]',
                'title': 'div:nth-child(1) > h3 > a::text',
                'link': 'div:nth-child(1) > h3 > a::attr(href)',
                'snippet': 'font > a::text',
                'visible_link': 'div:nth-child(2) > a > span'
            },
        }, 

        'ads_top': {
            '0': {
                'container': '#content_left',
                'result_container': '#4001',
                'link': 'h3 > a::attr(href)',
                'snippet': 'div > a::text',
                'title': 'h3 > a::text',
                'visible_link': 'a > span::text'
            },
            '1': {
                'container': '#content_left',
                'result_container': 'div[id^="40"]',
                'title': 'div:nth-child(1) > h3 > a::text',
                'link': 'div:nth-child(1) > h3 > a::attr(href)',
                'snippet': 'div:nth-child(2) > div:nth-child(1) > div:nth-child(2)',
                'visible_link': 'div:nth-child(3) > a > span'
            },
            '2': {
                'container': '#content_left',
                'result_container': 'div[id^="40"]',
                'title': 'div:nth-child(1) > h3 > a::text',
                'link': 'div:nth-child(1) > h3 > a::attr(href)',
                'snippet': 'div:nth-child(2) > a::text',
                'visible_link': 'div:nth-child(3) > a > span'
            },
            '3': {
                'container': '#content_left',
                'result_container': 'div[id^="40"]',
                'title': 'div:nth-child(1) > h3 > a::text',
                'link': 'div:nth-child(1) > h3 > a::attr(href)',
                'snippet': 'div:nth-child(2) > table div > font > a::text',
                'visible_link': 'div:nth-child(3) > a > span'
            },
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def after_parsing(self):
        """Clean the urls.

        href="/i?ct=503316480&z=&tn=baiduimagedetail&ipn=d&word=matterhorn&step_word=&ie=utf-8&in=9250&
        cl=2&lm=-1&st=&cs=3326243323,1574167845&os=1495729451,4260959385&pn=0&rn=1&di=69455168860&ln=1285&
        fr=&&fmq=1419285032955_R&ic=&s=&se=&sme=0&tab=&width=&height=&face=&is=&istype=&ist=&jit=&
        objurl=http%3A%2F%2Fa669.phobos.apple.com%2Fus%2Fr1000%2F077%2FPurple%2F\
        v4%2F2a%2Fc6%2F15%2F2ac6156c-e23e-62fd-86ee-7a25c29a6c72%2Fmzl.otpvmwuj.1024x1024-65.jpg&adpicid=0"
        """
        super().after_parsing()

        # Extract the domain from the visible link since Baidu always
        # redirects through its own domain.
        for key, i in self.iter_serp_items():
            # HTML hard to pin down for now. Just delete incorrectly scraped elements. 
            if not any([self.search_results[key][i]['title'],
                        self.search_results[key][i]['snippet'],
                        self.search_results[key][i]['visible_link']]):
                del self.search_results[key][i]
                continue
                
            if self.search_results[key][i]['visible_link']:
                vlink = str.strip(self.search_results[key][i]['visible_link'])
                try:
                    vlink = vlink.split()[0]
                except:
                    pass
                self.search_results[key][i]['visible_link'] = 'http://' + vlink
                self.search_results[key][i]['link'] = self.search_results[key][i]['visible_link']
                        
        if self.search_engine == 'normal':
            if len(self.dom.xpath(self.css_to_xpath('.hit_top_new'))) >= 1:
                self.no_results = True

            for key, i in self.iter_serp_items():
                if self.search_results[key][i]['visible_link'] is None:
                    del self.search_results[key][i]

                
class YandexParser(Parser):
    """Parses SERP pages of the Yandex search engine."""

    search_engine = 'yandex'

    search_types = ['normal', 'image']

    no_results_selector = ['.message .misspell__message::text']

    effective_query_selector = ['.misspell__message .misspell__link']

    num_results_search_selectors = ['.serp-adv .serp-item__wrap > strong']

    page_number_selectors = ['.pager__group .button_checked_yes span::text']

    normal_search_selectors = {
        'organic': {
            'de_ip': {
                'container': 'div.serp-list',
                'result_container': 'div.serp-item__wrap ',
                'link': 'a.serp-item__title-link::attr(href)',
                'snippet': 'div.serp-item__text::text',
                'title': 'a.serp-item__title-link::text',
                'visible_link': 'a.serp-url__link::attr(href)'
            }
        }
    }

    image_search_selectors = {
        'organic': {
            'de_ip': {
                'container': '.page-layout__content-wrapper',
                'result_container': '.serp-item__preview',
                'link': '.serp-item__preview .serp-item__link::attr(onmousedown)'
            },
            'de_ip_raw': {
                'container': '.page-layout__content-wrapper',
                'result_container': '.serp-item__preview',
                'link': '.serp-item__preview .serp-item__link::attr(href)'
            }
        }
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def after_parsing(self):
        """Clean the urls.

        Normally Yandex image search store the image url in the onmousedown attribute in a json object. Its
        pretty messsy. This method grabs the link with a quick regex.

        c.hit({"dtype":"iweb","path":"8.228.471.241.184.141","pos":69,"reqid":\
        "1418919408668565-676535248248925882431999-ws35-986-IMG-p2"}, \
        {"href":"http://www.thewallpapers.org/wallpapers/3/382/thumb/600_winter-snow-nature002.jpg"});

        Sometimes the img url is also stored in the href attribute (when requesting with raw http packets).
        href="/images/search?text=snow&img_url=\
        http%3A%2F%2Fwww.proza.ru%2Fpics%2F2009%2F12%2F07%2F1290.jpg&pos=2&rpt=simage&pin=1">
        """
        super().after_parsing()

        if self.searchtype == 'normal':
            self.no_results = False

            if self.no_results_text:
                self.no_results = 'По вашему запросу ничего не нашлось' in self.no_results_text

            if self.num_results == 0:
                self.no_results = True

        if self.searchtype == 'image':
            for key, i in self.iter_serp_items():
                for regex in (
                        r'\{"href"\s*:\s*"(?P<url>.*?)"\}',
                        r'img_url=(?P<url>.*?)&'
                ):
                    result = re.search(regex, self.search_results[key][i]['link'])
                    if result:
                        self.search_results[key][i]['link'] = result.group('url')
                        break


class BingParser(Parser):
    """Parses SERP pages of the Bing search engine."""

    search_engine = 'bing'

    search_types = ['normal', 'image']

    no_results_selector = ['#b_results > .b_ans::text']

    num_results_search_selectors = ['.sb_count']

    effective_query_selector = ['#sp_requery a > strong']

    page_number_selectors = ['.sb_pagS::text']

    normal_search_selectors = {
        'organic': {
            'us_ip': {
                'container': '#b_results',
                'result_container': '.b_algo',
                'link': 'h2 > a::attr(href)',
                'snippet': '.b_caption > p::text',
                'title': 'h2::text',
                'visible_link': 'cite::text'
            },
            'de_ip': {
                'container': '#b_results',
                'result_container': '.b_algo',
                'link': 'h2 > a::attr(href)',
                'snippet': '.b_caption > p::text',
                'title': 'h2::text',
                'visible_link': 'cite::text'
            },
            'de_ip_news_items': {
                'container': 'ul.b_vList li',
                'link': ' h5 a::attr(href)',
                'snippet': 'p::text',
                'title': ' h5 a::text',
                'visible_link': 'cite::text'
            },
        },
        'ads_main': {
            'us_ip': {
                'container': '#b_results .b_ad',
                'result_container': '.sb_add',
                'link': 'h2 > a::attr(href)',
                'snippet': '.sb_addesc::text',
                'title': 'h2 > a::text',
                'visible_link': 'cite::text'
            },
            'de_ip': {
                'container': '#b_results .b_ad',
                'result_container': '.sb_add',
                'link': 'h2 > a::attr(href)',
                'snippet': '.b_caption > p::text',
                'title': 'h2 > a::text',
                'visible_link': 'cite::text'
            }
        }
    }

    image_search_selectors = {
        'organic': {
            'ch_ip': {
                'container': '#dg_c .imgres',
                'result_container': '.dg_u',
                'link': 'a.dv_i::attr(m)'
            },
        }
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def after_parsing(self):
        """Clean the urls.

        The image url data is in the m attribute.

        m={ns:"images.1_4",k:"5018",mid:"46CE8A1D71B04B408784F0219B488A5AE91F972E",
        surl:"http://berlin-germany.ca/",imgurl:"http://berlin-germany.ca/images/berlin250.jpg",
        oh:"184",tft:"45",oi:"http://berlin-germany.ca/images/berlin250.jpg"}
        """
        super().after_parsing()

        if self.searchtype == 'normal':

            self.no_results = False
            if self.no_results_text:
                self.no_results = self.query in self.no_results_text \
                    or 'Do you want results only for' in self.no_results_text

        if self.searchtype == 'image':
            for key, i in self.iter_serp_items():
                for regex in (
                        r'imgurl:"(?P<url>.*?)"',
                ):
                    result = re.search(regex, self.search_results[key][i]['link'])
                    if result:
                        self.search_results[key][i]['link'] = result.group('url')
                        break


class YahooParser(Parser):
    """Parses SERP pages of the Yahoo search engine."""

    search_engine = 'yahoo'

    search_types = ['normal', 'image']

    no_results_selector = []

    # yahooo doesn't have such a thing :D
    effective_query_selector = ['']

    num_results_search_selectors = ['#pg > span:last-child']

    page_number_selectors = ['#pg > strong::text']

    normal_search_selectors = {
        'organic': {
            'de_ip': {
                'container': '#main',
                'result_container': '.res',
                'link': 'div > h3 > a::attr(href)',
                'snippet': 'div.abstr::text',
                'title': 'div > h3 > a::text',
                'visible_link': 'span.url::text'
            }
        },
    }

    image_search_selectors = {
        'organic': {
            'ch_ip': {
                'container': '#results',
                'result_container': '#sres > li',
                'link': 'a::attr(href)'
            },
        }
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def after_parsing(self):
        """Clean the urls.

        The url is in the href attribute and the &imgurl= parameter.

        <a id="yui_3_5_1_1_1419284335995_1635" aria-label="<b>Matterhorn</b> sunrise"
        href="/images/view;_ylt=AwrB8phvj5hU7moAFzOJzbkF;_ylu=\
        X3oDMTIyc3ZrZ3RwBHNlYwNzcgRzbGsDaW1nBG9pZANmNTgyY2MyYTY4ZmVjYTI5YmYwNWZlM2E3ZTc1YzkyMARncG9zAzEEaXQDYmluZw--?
        .origin=&back=https%3A%2F%2Fimages.search.yahoo.com%2Fsearch%2Fimages%3F\
        p%3Dmatterhorn%26fr%3Dyfp-t-901%26fr2%3Dpiv-web%26tab%3Dorganic%26ri%3D1&w=4592&h=3056&
        imgurl=www.summitpost.org%2Fimages%2Foriginal%2F699696.JPG&rurl=http%3A%2F%2Fwww.summitpost.org\
        %2Fmatterhorn-sunrise%2F699696&size=5088.0KB&
        name=%3Cb%3EMatterhorn%3C%2Fb%3E+sunrise&p=matterhorn&oid=f582cc2a68feca29bf05fe3a7e75c920&fr2=piv-web&
        fr=yfp-t-901&tt=%3Cb%3EMatterhorn%3C%2Fb%3E+sunrise&b=0&ni=21&no=1&ts=&tab=organic&
        sigr=11j056ue0&sigb=134sbn4gc&sigi=11df3qlvm&sigt=10pd8j49h&sign=10pd8j49h&.crumb=qAIpMoHvtm1&\
        fr=yfp-t-901&fr2=piv-web">
        """
        super().after_parsing()

        if self.searchtype == 'normal':

            self.no_results = False
            if self.num_results == 0:
                self.no_results = True

            if len(self.dom.xpath(self.css_to_xpath('#cquery'))) >= 1:
                self.no_results = True

            for key, i in self.iter_serp_items():
                if self.search_results[key][i]['visible_link'] is None:
                    del self.search_results[key][i]

        if self.searchtype == 'image':
            for key, i in self.iter_serp_items():
                for regex in (
                        r'&imgurl=(?P<url>.*?)&',
                ):
                    result = re.search(regex, self.search_results[key][i]['link'])
                    if result:
                        # TODO: Fix this manual protocol adding by parsing "rurl"
                        self.search_results[key][i]['link'] = 'http://' + unquote(result.group('url'))
                        break
                

class DuckduckgoParser(Parser):
    """Parses SERP pages of the Duckduckgo search engine."""

    search_engine = 'duckduckgo'

    search_types = ['normal']

    num_results_search_selectors = []

    no_results_selector = []

    effective_query_selector = ['']

    # duckduckgo is loads next pages with ajax
    page_number_selectors = ['']

    normal_search_selectors = {
        'organic': {
            'de_ip': {
                'container': '#links',
                'result_container': '.result',
                'link': '.result__title > a::attr(href)',
                'snippet': 'result__snippet::text',
                'title': '.result__title > a::text',
                'visible_link': '.result__url__domain::text'
            }
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def after_parsing(self):
        super().after_parsing()

        if self.searchtype == 'normal':

            try:
                if 'No more results.' in self.dom.xpath(self.css_to_xpath('.no-results'))[0].text_content():
                    self.no_results = True
            except:
                pass

            if self.num_results > 0:
                self.no_results = False
            elif self.num_results <= 0:
                self.no_results = True


class AskParser(Parser):
    """Parses SERP pages of the Ask search engine."""

    search_engine = 'ask'

    search_types = ['normal']

    num_results_search_selectors = []

    no_results_selector = []

    effective_query_selector = ['#spell-check-result > a']

    page_number_selectors = ['.pgcsel .pg::text']

    normal_search_selectors = {
        'organic': {
            'de_ip': {
                'container': '#midblock',
                'result_container': '.ptbs.ur',
                'link': '.abstract > a::attr(href)',
                'snippet': '.abstract::text',
                'title': '.txt_lg.b::text',
                'visible_link': '.durl span::text'
            }
        },
    }


class BlekkoParser(Parser):
    """Parses SERP pages of the Blekko search engine."""

    search_engine = 'blekko'

    search_types = ['normal']

    effective_query_selector = ['']

    no_results_selector = []

    num_results_search_selectors = []

    normal_search_selectors = {
        'organic': {
            'de_ip': {
                'container': '#links',
                'result_container': '.result',
                'link': '.result__title > a::attr(href)',
                'snippet': 'result__snippet::text',
                'title': '.result__title > a::text',
                'visible_link': '.result__url__domain::text'
            }
        },
    }

class YouTubeParser(Parser):
    """Parses SERP pages of the YouTube search engine :D."""

    search_engine = 'youtube'

    search_types = ['normal']

    effective_query_selector = ['']

    no_results_selector = []

    num_results_search_selectors = []

    normal_search_selectors = {
        'organic': {
            'us_ip': {
                'container': '#result', 
                'result_container': 'yt-lockup-content',
                'link': 'yt-lockup-title > a::attr(href)',
                'snippet': 'yt-lockup-description yt-ui-ellipsis yt-ui-ellipsis-2',
                'title': 'yt-lockup-title > a::text',
                'user': 'yt-lockup-byline > a::text',
                'profile_url': 'yt-lockup-byline > a::attr(href)'
            }
        },
        'sponsored_ads': { # These don't work because sponsored ads are loaded via javascript
            'us_ip': {
                'container': '.pyv-afc-ads-inner', 
                'result_container': '.yt-lockup-content',
                'link': '.yt-lockup-title > a::attr(href)',
                'snippet': '.yt-lockup-description.yt-ui-ellipsis.yt-ui-ellipsis-2',
                'title': '.yt-lockup-title > a::text',
                'user': '.yt-lockup-byline > a::text',
                'profile_url': '.yt-lockup-byline > a::attr(href)'
            }
        }
    }

class YouTubeSponsoredParser(Parser):
    """Parses SERP pages of the YouTube (sponsored) search engine :D."""

    search_engine = 'youtube_sponsored'

    search_types = ['normal']

    effective_query_selector = ['']

    no_results_selector = []

    num_results_search_selectors = []

    normal_search_selectors = {
        'sponsored_ads': { # Needs Selenium because sponsored ads are loaded by script
            'us_ip': {
                'container': '.pyv-afc-ads-inner', 
                'result_container': '.yt-lockup-content',
                'link': '.yt-lockup-title > a::attr(snippet)',
                'href': '.yt-lockup-description.yt-ui-ellipsis.yt-ui-ellipsis-2',
                'title': '.yt-lockup-title > a::text',
                'user': '.yt-lockup-byline > a::text',
                'profile_url': '.yt-lockup-byline > a::attr(href)'
            }
        }
    }

    
def get_parser_by_url(url):
    """Get the appropriate parser by an search engine url.

    Args:
        url: The url that was used to issue the search

    Returns:
        The correct parser that can parse results for this url.

    Raises:
        UnknowUrlException if no parser could be found for the url.
    """
    parser = None

    if re.search(r'^http[s]?://www\.google', url):
        parser = GoogleParser
    elif re.search(r'^http://yandex\.ru', url):
        parser = YandexParser
    elif re.search(r'^http://www\.bing\.', url):
        parser = BingParser
    elif re.search(r'^http[s]?://search\.yahoo.', url):
        parser = YahooParser
    elif re.search(r'^http://www\.baidu\.com', url):
        parser = BaiduParser
    elif re.search(r'^https://duckduckgo\.com', url):
        parser = DuckduckgoParser
    if re.search(r'^http[s]?://[a-z]{2}?\.ask', url):
        parser = AskParser
    if re.search(r'^http[s]?://blekko', url):
        parser = BlekkoParser
    #if re.search(r'^http[s]?://www\.youtube', url):
    #    parser = YouTubeParser
    if not parser:
        raise UnknowUrlException('No parser for {}.'.format(url))

    return parser

def is_this_search_engine(search_engine, matching_search_engines):
    """A predicate meant to centralize all search engine token
    comparisons.

    """
    return search_engine.lower() in map(lambda s: s.lower(), matching_search_engines)

def get_parser_by_search_engine(search_engine):
    """Get the appropriate parser for the search_engine

    Args:
        search_engine: The name of a search_engine.

    Returns:
        A parser for the search_engine

    Raises:
        NoParserForSearchEngineException if no parser could be found for the name.
    """
    if is_this_search_engine(search_engine, ['google', 'googleimg']):
        return GoogleParser
    elif search_engine == 'baidu' or search_engine == 'baiduimg':
        return BaiduParser
    # The following branches ought be expunged vigorously and with
    # great flourish. The exception may be Yandex, but the Russia team
    # has become mysteriously quiet, even absent. Perhaps His
    # Excellency Putin was displeased...
    elif is_this_search_engine(search_engine, ['yandex']):
        return YandexParser
    elif is_this_search_engine(search_engine, ['bing']):
        return BingParser
    elif is_this_search_engine(search_engine, ['yahoo']):
        return YahooParser
    elif is_this_search_engine(search_engine, ['duckduckgo']):
        return DuckduckgoParser
    elif is_this_search_engine(search_engine, ['ask']):
        return AskParser
    elif is_this_search_engine(search_engine, ['blekko']):
        return BlekkoParser
    elif is_this_search_engine(search_engine, ['youtube']):
        return YouTubeParser
    elif is_this_search_engine(search_engine, ['youtube_sponsored']):
        return YouTubeSponsoredParser
    else:
        raise NoParserForSearchEngineException('No such parser for {}'.format(search_engine))

    
def parse_serp(html=None, parser=None, scraper=None, search_engine=None, query=''):
    """Store the parsed data in the sqlalchemy session.

    If no parser is supplied then we are expected to parse again with
    the provided html.

    This function may be called from scraping and caching.
    When called from caching, some info is lost (like current page number).

    Args:
        TODO: A whole lot

    Returns:
        The parsed SERP object.
    """

    if not parser and html:
        parser = get_parser_by_search_engine(search_engine)
        parser = parser(query=query)
        parser.parse(html)

    serp = SearchEngineResultsPage()

    serp.id = generate_id()
    
    if query:
        serp.query = query

    if parser:
        serp.set_values_from_parser(parser)
    if scraper:
        serp.set_values_from_scraper(scraper)

    return serp


def get_domain_if_present(domain_str):
    """Extracts the domain name. This functions assumed the extracted
    domain is valid. Under that assumption, it tries to slice out the
    largest string that could be the domain (motivated by
    irregularities in fucking Baidu search results).

    """
    m = re.search("(?P<url>https?://[^\s]+)", domain_str)
    if m:
        return m.group("url")


if __name__ == '__main__':
    """Originally part of https://github.com/NikolaiT/GoogleScraper.
    
    Only for testing purposes: May be called directly with an search engine 
    search url. For example:
    
    python3 parsing.py 'http://yandex.ru/yandsearch?text=GoogleScraper&lr=178&csg=82%2C4317%2C20%2C20%2C0%2C0%2C0'
    
    Please note: Using this module directly makes little sense, because requesting such urls
    directly without imitating a real browser (which is done in my GoogleScraper module) makes
    the search engines return crippled html, which makes it impossible to parse.
    But for some engines it nevertheless works (for example: yandex, google, ...).
    """
    import requests

    assert len(sys.argv) >= 2, 'Usage: {} url/file'.format(sys.argv[0])
    url = sys.argv[1]
    if os.path.exists(url):
        raw_html = open(url, 'r').read()
        parser = get_parser_by_search_engine(sys.argv[2])
    else:
        raw_html = requests.get(url).text
        parser = get_parser_by_url(url)

    parser = parser(raw_html)
    parser.parse()
    print(parser)

    with open('/tmp/testhtml.html', 'w') as of:
        of.write(raw_html)
